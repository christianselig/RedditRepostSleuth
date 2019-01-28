import threading
from time import sleep

import praw
import os

from sqlalchemy import create_engine

from redditrepostsleuth.db.uow.sqlalchemyunitofworkmanager import SqlAlchemyUnitOfWorkManager
from redditrepostsleuth.service.imagerepost import ImageRepostProcessing
from redditrepostsleuth.service.postIngest import PostIngest

reddit = praw.Reddit(
    client_id=os.getenv('redditclientid'),
    client_secret=os.getenv('redditsecret'),
    password=os.getenv('redditpass'),
    user_agent='testscript by /u/fakebot3',
    username=os.getenv('reddituser')
)

db_engine = create_engine('mysql+pymysql://{}:{}@{}/{}'.format(os.getenv('DB_USER'),
                                                               os.getenv('DB_PASS'),
                                                               os.getenv('DB_HOST'),
                                                               'reddit'))

hashing = ImageRepostProcessing(SqlAlchemyUnitOfWorkManager(db_engine))
threading.Thread(target=hashing.generate_hashes).start()
threading.Thread(target=hashing.clear_deleted_images).start()

ingest = PostIngest(reddit, SqlAlchemyUnitOfWorkManager(db_engine))
threading.Thread(target=ingest.run).start()
while True:
    sleep(5)