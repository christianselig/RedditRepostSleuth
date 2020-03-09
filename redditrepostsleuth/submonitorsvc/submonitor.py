import time

from praw.exceptions import APIException
from praw.models import Submission, Comment
from prawcore import Forbidden
from redlock import RedLockError
from time import perf_counter
from sqlalchemy.exc import IntegrityError

from redditrepostsleuth.core.config import Config
from redditrepostsleuth.core.db.databasemodels import Post, MonitoredSub, MonitoredSubChecks
from redditrepostsleuth.core.db.uow.sqlalchemyunitofworkmanager import SqlAlchemyUnitOfWorkManager
from redditrepostsleuth.core.duplicateimageservice import DuplicateImageService
from redditrepostsleuth.core.exception import NoIndexException, RateLimitException, InvalidImageUrlException
from redditrepostsleuth.core.logging import log
from redditrepostsleuth.core.model.events.sub_monitor_event import SubMonitorEvent
from redditrepostsleuth.core.model.imagerepostwrapper import ImageRepostWrapper
from redditrepostsleuth.core.services.eventlogging import EventLogging
from redditrepostsleuth.core.services.reddit_manager import RedditManager
from redditrepostsleuth.core.services.response_handler import ResponseHandler
from redditrepostsleuth.core.services.responsebuilder import ResponseBuilder
from redditrepostsleuth.core.util.helpers import build_msg_values_from_search, build_image_msg_values_from_search
from redditrepostsleuth.core.util.objectmapping import submission_to_post
from redditrepostsleuth.core.util.reposthelpers import check_link_repost
from redditrepostsleuth.ingestsvc.util import pre_process_post


class SubMonitor:

    def __init__(
            self,
            image_service: DuplicateImageService,
            uowm: SqlAlchemyUnitOfWorkManager,
            reddit: RedditManager,
            response_builder: ResponseBuilder,
            response_handler: ResponseHandler,
            event_logger: EventLogging = None,
            config: Config = None
    ):
        self.image_service = image_service
        self.uowm = uowm
        self.reddit = reddit
        self.response_builder = response_builder
        self.resposne_handler = response_handler
        self.event_logger = event_logger
        if config:
            self.config = config
        else:
            self.config = Config()

    def run(self):
        while True:
            try:
                with self.uowm.start() as uow:
                    monitored_subs = uow.monitored_sub.get_all()
                    for sub in monitored_subs:
                        if not sub.active:
                            log.debug('Sub %s is disabled', sub.name)
                            continue
                        self._check_sub(sub)
                log.info('Sleeping until next run')
                time.sleep(60)
            except Exception as e:
                log.exception('Sub monitor service crashed', exc_info=True)

    def _should_check_post(self, submission: Submission):
        with self.uowm.start() as uow:
            checked = uow.monitored_sub_checked.get_by_id(submission.id)
            if checked:
                return False
            post = uow.posts.get_by_post_id(submission.id)
            if not post:
                post = self.save_unknown_post(submission.id)
                if not post:
                    log.info('Post %s has not been ingested yet.  Skipping')
                    return False

        if post.left_comment:
            return False

        if post.post_type not in self.config.supported_post_types:
            return False

        if post.crosspost_parent:
            log.debug('Skipping crosspost')
            return False

        return True

    def check_submission(self, submission: Submission, monitored_sub: MonitoredSub):
        if not self._should_check_post(submission):
            return

        with self.uowm.start() as uow:
            post = uow.posts.get_by_post_id(submission.id)

        if post.post_type == 'image' and post.dhash_h is None:
            log.error('Post %s has no dhash', post.post_id)
            return

        try:
            if post.post_type == 'image':
                search_results = self._check_for_repost(post, monitored_sub)
            elif post.post_type == 'link':
                search_results = self._check_for_link_repost(post)
        except NoIndexException:
            log.error('No search index available.  Cannot check post %s in %s', submission.id, submission.subreddit.display_name)
            return
        except RedLockError:
            log.error('New search index is being loaded. Cannot check post %s in %s', submission.id, submission.subreddit.display_name)
            return

        if not search_results.matches and monitored_sub.repost_only:
            log.debug('No matches for post %s and comment OC is disabled',
                     f'https://redd.it/{search_results.checked_post.post_id}')
            self._create_checked_post(post)
            return

        try:
            comment = self._leave_comment(search_results, submission, monitored_sub)
        except APIException as e:
            error_type = None
            if hasattr(e, 'error_type'):
                error_type = e.error_type
            log.exception('Praw API Exception.  Error Type: %s', error_type, exc_info=True)
            return
        except RateLimitException:
            time.sleep(10)
            return
        except Exception as e:
            log.exception('Failed to leave comment on %s in %s', submission.id, submission.subreddit.display_name)
            return

        self._sticky_reply(monitored_sub, comment)
        self._mark_post_as_comment_left(post)
        self._create_checked_post(post)

        if search_results.matches:
            self._report_submission(monitored_sub, submission)


    def _check_sub(self, monitored_sub: MonitoredSub):
        log.info('Checking sub %s', monitored_sub.name)
        start_time = perf_counter()
        subreddit = self.reddit.subreddit(monitored_sub.name)
        if not subreddit:
            log.error('Failed to get Subreddit %s', monitored_sub.name)
            return

        submissions = subreddit.new(limit=monitored_sub.search_depth)
        checked_posts = 0
        for submission in submissions:
            if not self._should_check_post(submission):
                continue

            with self.uowm.start() as uow:
                post = uow.posts.get_by_post_id(submission.id)

            if post.post_type == 'image' and post.dhash_h is None:
                log.error('Post %s has no dhash', post.post_id)
                continue
            checked_posts += 1
            try:
                if post.post_type == 'image':
                    search_results = self._check_for_repost(post, monitored_sub)
                elif post.post_type == 'link':
                    search_results = self._check_for_link_repost(post)
            except NoIndexException:
                log.error('No search index available.  Cannot check post %s in %s', submission.id, submission.subreddit.display_name)
                continue
            except RedLockError:
                log.error('New search index is being loaded. Cannot check post %s in %s', submission.id, submission.subreddit.display_name)
                continue

            if not search_results.matches and monitored_sub.repost_only:
                log.debug('No matches for post %s and comment OC is disabled',
                         f'https://redd.it/{search_results.checked_post.post_id}')
                self._create_checked_post(post)
                continue

            try:
                comment = self._leave_comment(search_results, submission, monitored_sub)
            except APIException as e:
                error_type = None
                if hasattr(e, 'error_type'):
                    error_type = e.error_type
                log.exception('Praw API Exception.  Error Type: %s', error_type, exc_info=True)
                continue
            except RateLimitException:
                time.sleep(10)
                continue
            except Exception as e:
                log.exception('Failed to leave comment on %s in %s', submission.id, submission.subreddit.display_name)
                continue

            self._sticky_reply(monitored_sub, comment)
            self._mark_post_as_comment_left(post)
            self._create_checked_post(post)

            if search_results.matches:
                self._report_submission(monitored_sub, submission)

        process_time = perf_counter() - start_time
        if self.event_logger:
            self.log_run(process_time, checked_posts, monitored_sub.name)

    def _mark_post_as_comment_left(self, post: Post):
        try:
            with self.uowm.start() as uow:
                post.left_comment = True
                uow.posts.update(post)
                uow.commit()
        except Exception as e:
            log.exception('Failed to mark post %s as checked', post.id, exc_info=True)

    def _create_checked_post(self, post: Post):
        try:
            with self.uowm.start() as uow:
                uow.monitored_sub_checked.add(
                    MonitoredSubChecks(post_id=post.post_id, subreddit=post.subreddit)
                )
                uow.commit()
        except Exception as e:
            log.exception('Failed to create checked post for submission %s', post.post_id, exc_info=True)

    def _check_for_link_repost(self, post: Post):
        return check_link_repost(post, self.uowm, get_total=True)

    def _check_for_repost(self, post: Post, monitored_sub: MonitoredSub) -> ImageRepostWrapper:
        """
        Check if provided post is a repost
        :param post: DB Post obj
        :return: None
        """

        search_results = self.image_service.check_duplicates_wrapped(
            post,
            target_annoy_distance=monitored_sub.target_annoy,
            target_hamming_distance=monitored_sub.target_hamming,
            date_cutoff=monitored_sub.target_days_old,
            same_sub=monitored_sub.same_sub_only,
            meme_filter=monitored_sub.meme_filter,
            source='sub_monitor'
        )

        log.debug(search_results)
        return search_results

    def _sticky_reply(self, monitored_sub: MonitoredSub, comment: Comment):
        if monitored_sub.sticky_comment:
            try:
                comment.mod.distinguish(sticky=True)
                log.info('Made comment %s sticky', comment.id)
            except Forbidden:
                log.error('Failed to sticky comment, no permissions')
            except Exception as e:
                log.exception('Failed to sticky comment', exc_info=True)

    def _remove_post(self, monitored_sub: MonitoredSub, post: Post):
        pass

    def _lock_post(self, monitored_sub: MonitoredSub, post: Post):
        pass

    def _mark_post_as_oc(self, monitored_sub: MonitoredSub, post: Post):
        pass


    def _report_submission(self, monitored_sub: MonitoredSub, submission: Submission):
        if not monitored_sub.report_submission:
            return
        log.info('Reporting post %s on %s', f'https://redd.it/{submission.id}', monitored_sub.name)
        try:
            submission.report(monitored_sub.report_msg)
        except Exception as e:
            log.exception('Failed to report submissioni', exc_info=True)

    def _leave_comment(self, search_results: ImageRepostWrapper, submission: Submission, monitored_sub: MonitoredSub) -> Comment:

        msg_values = build_msg_values_from_search(search_results, self.uowm, target_days_old=monitored_sub.target_days_old)
        if search_results.checked_post.post_type == 'image':
            msg_values = build_image_msg_values_from_search(search_results, self.uowm, **msg_values)

        if search_results.matches:
            msg = self.response_builder.build_sub_repost_comment(
                search_results.checked_post.subreddit,
                msg_values,
                search_results.checked_post.post_type
            )
        else:
            msg = self.response_builder.build_sub_oc_comment(
                search_results.checked_post.subreddit,
                msg_values, search_results.checked_post.post_type
            )

        return self.resposne_handler.reply_to_submission(submission.id, msg)

    def save_unknown_post(self, post_id: str) -> Post:
        """
        If we received a request on a post we haven't ingest save it
        :param submission: Reddit Submission
        :return:
        """
        log.info('Post %s does not exist, attempting to ingest', post_id)
        submission = self.reddit.submission(post_id)
        post = None
        try:
            post = pre_process_post(submission_to_post(submission), self.uowm, None)
        except InvalidImageUrlException:
            log.error('Failed to ingest post %s.  URL appears to be bad', post_id)
        if not post:
            log.error('Problem ingesting post.  Either failed to save or it is not an image')
            return

        return post


    def log_run(self, process_time: float, post_count: int, subreddit: str):
        self.event_logger.save_event(
            SubMonitorEvent(
                event_type='subreddit_monitor',
                process_time=process_time,
                post_count=post_count,
                subreddit=subreddit
            )
        )
