import bbcode
import datetime
import json
import pickle
import pprint
import requests
import re
import textwrap
import time

import papercut.settings
import papercut.storage.strutil as strutil

from .body_massager import Body_Massager

settings = papercut.settings.CONF()
pp = pprint.PrettyPrinter(indent=2)

# when changing anything in self.forums or the reader routines,
# increment this to make the thing ditch the old pickle
MEGASTRUCTURE_VERSION=14

def slugify(str):
    return re.sub('[^a-z0-9]+', '-', str.lower())

# thanks https://python-3-patterns-idioms-test.readthedocs.io/en/latest/Singleton.html

class Borg:
    _shared_state = {}

    def __init__(self):
        self.__dict__ = self._shared_state

class XenforoCommon(Borg):
    initialized = False
    
    def __init__(self, api_key, api_url, spool):
        Borg.__init__(self)        
        if self.initialized:
            return
            
        self.requests_kwargs = {
            'headers': {
                'XF-Api-Key': api_key
            }
        }
        self.spool = spool
        self.api_url = api_url
        self.forums = {}
        self.posts_by_msgid = {}
        self.pending_attachment_ids = []
        self.attachments = []

        data = None
        try: 
            with open('%s/%s' % (self.spool, 'forums.pickle'), 'rb') as f:
                data = pickle.load(f)
        except Exception as e:
            print(e)

        if data is not None and 'version' in data and data['version'] == MEGASTRUCTURE_VERSION:
            self.forums = data['forums']
            self.attachments = data['attachments']
            print("loaded pickled forums, let's check for new stuff")
            self.get_everything()
        else:
            self.get_everything()

        self.index_posts()
        self.get_pending_attachments()
        self.dump_to_file()
        self.initialized = True

    def get_pending_attachments(self):
        print("fetching attachment metadata...")

        for attid in self.pending_attachment_ids:
            r = requests.get(self.api_url + ('/attachments/%s' % attid), **self.requests_kwargs)
            data = json.loads(r.text)
            self.attachments.append(data['attachment'])
            self.pending_attachment_ids.remove(attid)

    def index_posts(self):
        print("indexing starting...")

        self.posts_by_msgid = {}

        for forum in self.forums:
            # time-sorted array of all posts on this forum
            self.forums[forum]['posts'] = []
            allposts = []
            for thread in self.forums[forum]['threads']:
                allposts.extend(self.forums[forum]['threads'][thread]['posts'])
            allposts.sort(key=lambda item: item['post_date'])
            self.forums[forum]['posts'] = allposts

            # global dict with message id as key
            for post in self.forums[forum]['posts']:
                self.posts_by_msgid[post['nntp_message_id']] = post
            print("%s: indexed %d posts"  % (forum, len(self.forums[forum]['posts'])))

    def dump_to_file(self):
        with open('%s/%s' % (self.spool, '/forums.pickle'), 'wb') as f:
            pickle.dump({
                'version': MEGASTRUCTURE_VERSION,
                'forums': self.forums,
                'attachments': self.attachments
            }, f)
            print("dumped forums as pickle")

    def get_everything(self):
        self.get_forums()

    def get_forums(self, with_threads=True):
        r = requests.get(self.api_url + '/nodes/flattened', **self.requests_kwargs)
        data = json.loads(r.text)
        for node in data['nodes_flat']:
            if node['node']['node_type_id'] != 'Forum':
                continue
            slug = slugify(node['node']['title'])

            if slug in self.forums:
                # bail out and do not touch anything if nothing seems to have changed
                if self.forums[slug]['last_post_date'] == node['node']['type_data']['last_post_date']:
                    print("forum %s up to date, skipping" % slug)
                    continue
            else:
                self.forums[slug] = {}

            self.forums[slug]['threads'] = {}
            self.forums[slug]['id'] = node['node']['node_id']
            self.forums[slug]['description'] = node['node']['description']
            self.forums[slug]['message_count'] = node['node']['type_data']['message_count']
            self.forums[slug]['last_post_date'] = node['node']['type_data']['last_post_date']
            self.forums[slug]['last_post_id'] = node['node']['type_data']['last_post_id']
            self.forums[slug]['posts'] = []

            if with_threads:
                print("fetching threads for %s" % slug)
                self.get_threads_from_forum(slug)

    def get_threads_r(self, slug, page):
        forum_id = self.forums[slug]['id']
        r = requests.get('%s/forums/%d&page=%d&with_threads=1' % (self.api_url, forum_id, page),
                         **self.requests_kwargs)

        data = json.loads(r.text)
        for thread in data['threads']:
            # bail out and do not touch posts if nothing seems to have changed
            if (thread['thread_id'] in self.forums[slug]['threads'] and
                thread['last_post_id'] <= self.forums[slug]['threads'][thread['thread_id']]['last_post_id']):
                return
            else:
                self.forums[slug]['threads'][thread['thread_id']] = {
                    'title': thread['title'],
                    'last_post_date': thread['last_post_date'],
                    'last_post_id': thread['last_post_id'],
                    'first_post_nntp_message_id': '<%d.%d@forums.sgi.sh>' % (thread['post_date'], thread['first_post_id']),
                    'posts': []
                }

        if data['pagination']['last_page'] > page:
            self.get_threads_r(slug, page + 1)
        else:
            # no more threads to fetch, let's empty the forum's time-sorted post array
            # and figure out which posts to find.
            print("fetching posts for %s" % (slug))
            for thread_id, thread in self.forums[slug]['threads'].items():
                # bail out and do not touch posts if nothing seems to have changed
                if (thread['last_post_id'] <= self.forums[slug]['threads'][thread_id]['last_post_id'] and
                    len(self.forums[slug]['threads'][thread_id]['posts']) > 0):
                    continue
                self.forums[slug]['threads'][thread_id]['posts'].extend(self.get_posts_r(
                    thread_id=thread_id,
                    page=1,
                    nntp_subject=thread['title'],
                    nntp_group_name='sgug.%s' % slug,
                    nntp_references=thread['first_post_nntp_message_id']
                ))


    def get_threads_from_forum(self, slug):
        self.get_threads_r(slug, 1)

    def get_posts_r(self, thread_id, page, nntp_subject, nntp_group_name, nntp_references, posts=[]):
        ret = []
        r = requests.get('%s/threads/%d/posts&page=%d' % (self.api_url, thread_id, page),
                         **self.requests_kwargs)
        data = json.loads(r.text)
        for post in data['posts']:
            post['nntp_message_id'] = '<%d.%d@forums.sgi.sh>' % (post['post_date'], post['post_id'])
            if post['is_first_post']:
                post['nntp_subject'] = nntp_subject
                post['references'] = None
            else:
                post['nntp_subject'] = 'Re: %s' % nntp_subject
                post['references'] = nntp_references

            post['nntp_group_name'] = nntp_group_name

            if 'Attachments' in post:
                for att in post['Attachments']:
                    if att['attachment_id'] not in self.attachments:
                        self.pending_attachment_ids.append(att['attachment_id'])

            ret.append(post)

        if data['pagination']['last_page'] > page:
            return ret + self.get_posts_r(thread_id, page + 1, nntp_subject, nntp_group_name, nntp_references, posts)
        return ret

    def create_usenet_headers(self, post, id):
        headers = []
        headers.append("Path: %s" % (settings.nntp_hostname))
        headers.append("From: %s" % (post['username']))
        headers.append("Newsgroups: %s" % (post['nntp_group_name']))
        headers.append("Date: %s" % (strutil.get_formatted_time(time.localtime(post['post_date']))))
        headers.append("Subject: %s" % (post['nntp_subject']))
        headers.append("Message-ID: %s" % (post['nntp_message_id']))
        # easiest.
        #
        headers.append("Xref: %s %s:%s" % (settings.nntp_hostname, post['nntp_group_name'], id))
        if post['references']:
            headers.append("References: %s" % post['references'])
        return "\r\n".join(headers)

    def format_message(self, post):
        massager = Body_Massager()
        return massager.massage(post)
