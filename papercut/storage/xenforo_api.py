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
MEGASTRUCTURE_VERSION=9

def slugify(str):
    return re.sub('[^a-z0-9]+', '-', str.lower())

class Xenforo_NNTP:
    def __init__(self, api_key, api_url, spool):
        self.requests_kwargs = {
            'headers': {
                'XF-Api-Key': api_key
            }
        }
        self.spool = spool
        self.api_url = api_url
        self.forums = {}
        self.posts_by_msgid = {}

        data = None
        try: 
            with open('%s/%s' % (self.spool, 'forums.pickle'), 'rb') as f:
                data = pickle.load(f)
        except Exception as e:
            print(e)

        if data is not None and 'version' in data and data['version'] == MEGASTRUCTURE_VERSION:
            self.forums = data['data']
            print("loaded pickled forums, let's check for new stuff")
            self.get_everything()
        else:
            self.get_everything()

        self.index_posts()
        self.dump_to_file()

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
                'data': self.forums
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
            ret.append(post)

        if data['pagination']['last_page'] > page:
            return ret + self.get_posts_r(thread_id, page + 1, nntp_subject, nntp_group_name, nntp_references, posts)
        return ret

    def create_usenet_headers(self, post):
        headers = []
        headers.append("Path: %s" % (settings.nntp_hostname))
        headers.append("From: %s" % (post['username']))
        headers.append("Newsgroups: %s" % (post['nntp_group_name']))
        headers.append("Date: %s" % (strutil.get_formatted_time(time.localtime(post['post_date']))))
        headers.append("Subject: %s" % (post['nntp_subject']))
        headers.append("Message-ID: %s" % (post['nntp_message_id']))
        # easiest.
        #
        # headers.append("Xref: %s %s:%s" % (settings.nntp_hostname, post['nntp_group_name'], 1))
        if post['references']:
            headers.append("References: %s" % post['references'])
        return "\r\n".join(headers)

    def format_message(self, post):
        massager = Body_Massager()
        return massager.massage(post)
    
def decut(group_name):
    return re.sub('^sgug\.', '', group_name)
    
class Papercut_Storage:
    def __init__(self):
        self.api_key = settings.xenforo_api_key
        self.api_url = settings.xenforo_api_url
        self.spool = settings.xenforo_api_spool
        self.xn = Xenforo_NNTP(self.api_key, self.api_url, self.spool)

    def group_exists(self, group_name):
        if decut(group_name) in self.xn.forums:
            return True
        else:
            return False

    def get_message_id(self, msg_num, group_name):
        group = decut(group_name)
        return self.xn.forums[group]['posts'][int(msg_num) - 1]['nntp_message_id']
        
    def get_LIST(self, username=""):
        lists = []
        for group in self.xn.forums:
            msgcount = len(self.xn.forums[group]['posts'])
            lists.append("sgug.%s %s %s y" % (
                group,
                msgcount,
                msgcount
            ))
        return "\r\n".join(lists)

    def get_GROUP(self, group_name):
        group = decut(group_name)
        msgcount = len(self.xn.forums[group]['posts']) 
        min_mark = 1
        return (
            msgcount,
            min_mark,
            msgcount
        )

    def get_NEWGROUPS(self, ts, group='%'):
        # TODO: could be done
        return None
    
    def get_XOVER(self, group_name, start_id, end_id=100):
        group = decut(group_name)

        if len(self.xn.forums[group]['posts']) == 0:
            return ""
        
        overviews = []
        
        # message_number <tab> subject <tab> author <tab> date <tab> message_id <tab> reference <tab> bytes <tab> lines <tab> xref
        for index, post in enumerate(self.xn.forums[group]['posts'][int(start_id) - 1:int(end_id)]):
            if 'reference' in post:
                reference = post['reference']
            else:
                reference = ''
            msg_num = index + 1
            overviews.append("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
                msg_num,
                post['nntp_subject'],
                post['username'],
                strutil.get_formatted_time(time.localtime(post['post_date'])),
                post['nntp_message_id'],
                reference,
                8192,
                50,
                'Xref: %s %s:%s' % (settings.nntp_hostname, group_name, msg_num)
            ))

        return "\r\n".join(overviews)
        
    def get_HEAD(self, group_name, id):
        if id[0] == "0":
            return None
        elif id[0] == '<':
            return self.xn.create_usenet_headers(self.xn.posts_by_msgid[id])
        else:
            group = decut(group_name)
            return self.xn.create_usenet_headers(self.xn.forums[group]['posts'][int(id) - 1])

    def get_BODY(self, group_name, id):
        if id[0] == "0":
            return None
        elif id[0] == '<':
            return self.xn.format_message(self.xn.posts_by_msgid[id])
        else:
            group = decut(group_name)
            return self.xn.format_message(self.xn.forums[group]['posts'][int(id) - 1])

    def get_ARTICLE(self, group_name, id):
        return (
            self.get_HEAD(group_name, id),
            self.get_BODY(group_name, id)
        )
