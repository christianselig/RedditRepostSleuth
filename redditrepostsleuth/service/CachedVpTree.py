import sys

from distance import hamming

from redditrepostsleuth.common.logging import log
from redditrepostsleuth.db.uow.unitofworkmanager import UnitOfWorkManager
from datetime import datetime

from redditrepostsleuth.util.objectmapping import post_to_hashwrapper
from redditrepostsleuth.util.vptree import VPTree


class CashedVpTree:
    """
    Class to cache VP Tree of existing images
    """
    def __init__(self, uowm: UnitOfWorkManager):
        self.uowm = uowm
        self.tree_built_at = None
        self.vp_tree = None

    @property
    def get_tree(self):
        if self.tree_built_at:
            last_built_seconds = datetime.now() - self.tree_built_at
            log.info('Tree built %s seconds ago', last_built_seconds.seconds)
        if self.tree_built_at is None or (datetime.now() - self.tree_built_at).seconds > 1800:
            log.info('Building New VPTree')
            with self.uowm.start() as uow:
                existing_images = uow.posts.find_all_images_with_hash()
                log.info('Tree will be built with %s images', len(existing_images))
                log.info('Recurssion Depth: %s', print(sys.getrecursionlimit()))
                self.vp_tree = VPTree([post_to_hashwrapper(post) for post in existing_images], lambda x,y: hamming(x,y))
                self.tree_built_at = datetime.now()
                return self.vp_tree
        else:
            log.info('Returning cached VP Tree')
            return self.vp_tree