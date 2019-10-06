import threading
import time
from typing import List

from praw import Reddit
from praw.models import Submission
from prawcore import Forbidden

from redditrepostsleuth.common.celery.tasks import check_image_repost_save
from redditrepostsleuth.common.exception import ImageConversioinException
from redditrepostsleuth.common.logging import log
from redditrepostsleuth.common.config import config
from redditrepostsleuth.common.db.uow.unitofworkmanager import UnitOfWorkManager
from redditrepostsleuth.common.model.db import Post
from redditrepostsleuth.common.model.repostwrapper import RepostWrapper
from redditrepostsleuth.service.repostservicebase import RepostServiceBase
from redditrepostsleuth.common.util import set_image_hashes
from redditrepostsleuth.common.util.objectmapping import submission_to_post
# TODO - Deal with images that PIL can't convert.  Tons in database
from redditrepostsleuth.common.util.reposthelpers import sort_reposts


class ImageRepostService(RepostServiceBase):

    def __init__(self, uowm: UnitOfWorkManager, reddit: Reddit) -> None:
        super().__init__(uowm)
        self.reddit = reddit

    def start(self):
        log.info('Starting image repost thread')
        threading.Thread(target=self.repost_check, name='Repost Queue').start()


    def find_all_occurrences(self, submission: Submission) -> RepostWrapper:
        """
        Take a given Reddit submission and find all matching posts
        :param submission:
        :return:
        """
        # TODO: Change to use new repost checking functions

        with self.uowm.start() as uow:
            post = uow.posts.get_by_post_id(submission.id)
            if not post:
                post =  submission_to_post(submission)
                uow.posts.add(post)
                uow.commit()

        try:
            set_image_hashes(post)
        except Exception as e:
            # TODO: Specific exception
            log.exception('Problem in find_all_occurrences.  Exception type %s', str(type(e)), exc_info=True)
            ImageConversioinException('Failed to convert image to hash')

        results = check_image_repost_save.apply_async(args=(post,)).get()
        results.matches = sort_reposts(results.matches)
        return results

    def repost_check(self):
        offset = 0
        while True:
            try:
                with self.uowm.start() as uow:
                    posts = uow.posts.find_all_by_repost_check(False, limit=config.repost_image_batch_size, offset=offset)
                    if not posts:
                        break
                    for post in posts:
                        check_image_repost_save.s(post)

                    log.info('Waiting %s seconds until next repost batch', config.repost_image_batch_delay)
                    offset += config.repost_image_batch_size
                    time.sleep(config.repost_image_batch_delay)

            except Exception as e:
                log.exception('Repost thread died', exc_info=True)



    def _filter_matching_images(self, raw_list: List[Post], post_being_checked: Post) -> List[Post]:
        """
        Take a raw list if matched images.  Filter one ones meeting the following criteria.
            Same Author as post being checked - Gets rid of people posting to multiple subreddits
            If it has a crosspost parent - A cross post isn't considered a respost
            Same post ID as post being checked - The image list will contain the original image being checked
        :param raw_list: List of all matches
        :param post_being_checked: The posts we're checking is a repost
        """
        # TODO - Clean this up
        return [x for x in raw_list if x.post_id != post_being_checked.post_id and x.crosspost_parent is None and post_being_checked.author != x.author]


    def _handle_reposts(self, post: List[Post]) -> List[Post]:
        """
        Take a list of reposts and process them
        :param post: List of Posts
        """
        pass

    def _clean_reposts(self, posts: List[Post]) -> List[Post]:
        """
        Take a list of reposts, remove any cross posts and deleted posts
        :param posts: List of posts
        """
        posts = self._remove_crossposts(posts)
        posts = self._sort_reposts(posts)
        return posts


    def _sort_reposts(self, posts: List[Post], reverse=False) -> List[Post]:
        """
        Take a list of reposts and sort them by date
        :param posts:
        """
        return sorted(posts, key=lambda x: x.created_at, reverse=reverse)

    def _get_crosspost_parent(self, post: Post):
        submission = self.reddit.submission(id=post.post_id)
        if submission:
            try:
                result = submission.crosspost_parent
                log.debug('Post %s has corsspost parent %s', post.post_id, result)
                return result
            except (AttributeError,Forbidden):
                log.debug('No crosspost parent for post %s', post.post_id)
                return None
        log.error('Failed to find submission with ID %s', post.post_id)

    def _remove_crossposts(self, posts: List[Post]) -> List[Post]:
        results = []
        for post in posts:
            if post.checked_repost and post.crosspost_parent is None:
                results.append(post)
                continue

            submission = self.reddit.submission(id=post.post_id)
            if submission:
                try:
                    post.crosspost_parent = submission.crosspost_parent
                except AttributeError:
                    pass



                if post.crosspost_parent is None:
                    results.append(post)
                else:
                    with self.uowm.start() as uow:
                        uow.commit()


        return results