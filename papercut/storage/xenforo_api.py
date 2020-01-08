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

settings = papercut.settings.CONF()
pp = pprint.PrettyPrinter(indent=2)

# when changing anything in self.forums or the reader routines,
# increment this to make the thing ditch the old pickle
MEGASTRUCTURE_VERSION=6

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

        with open('%s/%s' % (self.spool, '/forums.pickle'), 'rb') as f:
            data = pickle.load(f)

            if 'version' in data and data['version'] == MEGASTRUCTURE_VERSION:
                self.forums = data['data']
                print("loaded pickled forums")
            else:
                self.get_everything()
                self.dump_to_file()

        print("indexing starting...")
        for forum in self.forums:
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

        for forum in self.forums:
            print("fetching threads for %s" % (forum))
            self.get_threads_from_forum(forum)
            self.forums[forum]['posts'] = []
            print("fetching posts for %s" % (forum))
            for thread_id, thread in self.forums[forum]['threads'].items():
                self.forums[forum]['posts'].extend(self.get_posts_r(
                    thread_id=thread_id,
                    page=1,
                    nntp_subject=thread['title'],
                    nntp_group_name='sgug.%s' % forum,
                    nntp_references=thread['first_post_nntp_message_id']
                ))
            self.forums[forum]['posts'].sort(key=lambda item: item['post_date'])
            
    def get_forums(self):
        r = requests.get(self.api_url + '/nodes/flattened', **self.requests_kwargs)
        data = json.loads(r.text)
        for node in data['nodes_flat']:
            if node['node']['node_type_id'] != 'Forum':
                continue
            slug = slugify(node['node']['title'])
            self.forums[slug] = {}
            self.forums[slug]['id'] = node['node']['node_id']
            self.forums[slug]['message_count'] = node['node']['type_data']['message_count']
            self.forums[slug]['last_post_date'] = node['node']['type_data']['last_post_date']
            self.forums[slug]['last_post_id'] = node['node']['type_data']['last_post_id']
            self.forums[slug]['last_updated_from_api'] = None
            self.forums[slug]['threads'] = {}
            self.forums[slug]['posts'] = []

    def get_threads_r(self, slug, page, newer_than):
        forum_id = self.forums[slug]['id']
        r = requests.get('%s/forums/%d&page=%d&with_threads=1' % (self.api_url, forum_id, page),
                         **self.requests_kwargs)

        data = json.loads(r.text)
        for thread in data['threads']:
            if newer_than is not None and thread['last_post_date'] < newer_than:
                return
            self.forums[slug]['threads'][thread['thread_id']] = {
                'title': thread['title'],
                'last_post_date': thread['last_post_date'],
                'first_post_nntp_message_id': '<%d.%d@forums.sgi.sh>' % (thread['post_date'], thread['first_post_id'])
            }
        if data['pagination']['last_page'] > page:
            self.get_threads_r(slug, page + 1, newer_than)
            
    def get_threads_from_forum(self, slug, newer_than=None, force=False):
        # avoid unnecessary API spam by not re-fetching data we should already have
        if force is False:
            forum_id = self.forums[slug]['id']
            r = requests.get('%s/forums/%d' % (self.api_url, forum_id),
                             **self.requests_kwargs)
            data = json.loads(r.text)
            if data['forum']['type_data']['last_post_date'] < (self.forums[slug]['last_updated_from_api'] or 0):
                return

        self.get_threads_r(slug, 1, newer_than)
        self.forums[slug]['last_updated_from_api'] = time.time()

    def get_posts_r(self, thread_id, page, nntp_subject, nntp_group_name, nntp_references, newer_than=0, posts=[]):
        ret = []
        r = requests.get('%s/threads/%d/posts&page=%d' % (self.api_url, thread_id, page),
                         **self.requests_kwargs)
        data = json.loads(r.text)
        for post in data['posts']:
            if post['post_date'] < newer_than:
                return ret
            else:
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
            return ret + self.get_posts_r(thread_id, page + 1, nntp_subject, nntp_group_name, nntp_references, newer_than, posts)
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

    def body_massage(self, post):
        parser = bbcode.Parser(
            install_defaults=False,
            newline="\n",
            escape_html=False,
            replace_links=False,
            replace_cosmetic=False,
            url_template="{text} <{href}>",
        )

        parser.add_simple_formatter('b', '_%(value)s_', escape_html=False)
        parser.add_simple_formatter('i', '_%(value)s_', escape_html=False)
        parser.add_simple_formatter('u', '_%(value)s_', escape_html=False)
        parser.add_simple_formatter('s', '*%(value)s*', escape_html=False)
        parser.add_simple_formatter('icode', '%(value)s', escape_html=False)
        parser.add_simple_formatter('code', '%(value)s', escape_html=False)
        parser.add_simple_formatter('size', '%(value)s', escape_html=False)
        parser.add_simple_formatter('user', '%(value)s', escape_html=False)
        parser.add_simple_formatter('font', '%(value)s', escape_html=False)
        parser.add_simple_formatter('img', '<%(value)s>\n', escape_html=False)
        parser.add_simple_formatter('hr', "\n-------------------------------------\n")
        parser.add_simple_formatter('list', '%(value)s')
        parser.add_simple_formatter('*', ' - %(value)s', escape_html=False)

        def _render_media(name, value, options, parent, context):
            if not options or 'MEDIA' not in options:
                return value
            if options['MEDIA'] == 'youtube':
                return '<https://youtu.be/%s>' % value
            elif options['MEDIA'] == 'reddit':
                return '<https://reddit.com/r/%s>' % value
            elif options['MEDIA'] == 'imgur':
                return '<https://imgur.com/%s>' % value
            else:
                print('unknown MEDIA, options: %s' % options)
                return value

        parser.add_formatter('media', _render_media, escape_html=False)
        
        # stuff stolen from bbcode.py because not easily reusable
        
        def _render_url(name, value, options, parent, context):
            # Adapted from http://daringfireball.net/2010/07/improved_regex_for_matching_urls
            # Changed to only support one level of parentheses, since it was failing catastrophically on some URLs.
            # See http://www.regular-expressions.info/catastrophic.html
            _url_re = re.compile(
                r"(?im)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)"
                r'(?:[^\s()<>]+|\([^\s()<>]+\))+(?:\([^\s()<>]+\)|[^\s`!()\[\]{};:\'".,<>?]))'
            )

            # For the URL tag, try to be smart about when to append a missing http://. If the given link looks like a domain,
            # add a http:// in front of it, otherwise leave it alone (since it may be a relative path, a filename, etc).
            _domain_re = re.compile(
                r"(?im)(?:www\d{0,3}[.]|[a-z0-9.\-]+[.](?:com|net|org|edu|biz|gov|mil|info|io|name|me|tv|us|uk|mobi))"
            )

            if options and "url" in options:
                href = options["url"]
                # Completely ignore javascript: and data: "links".
                if re.sub(r"[^a-z0-9+]", "", href.lower().split(":", 1)[0]) in ("javascript", "data", "vbscript"):
                    return ""
                # Only add the missing http:// if it looks like it starts with a domain name.
                if "://" not in href and _domain_re.match(href):
                    href = "http://" + href
                return parser.url_template.format(href=href.replace('"', "%22"), text=value)
            else:
                return '<%s>' % value


        parser.add_formatter("url", _render_url, replace_links=False, replace_cosmetic=False)

        # own work :----)
        def _render_quote(name, value, options, parent, context):
            # TODO: figure out depth

            wrapper = textwrap.TextWrapper(
                initial_indent="> ",
                subsequent_indent="> ",
                break_long_words=False
                )
            lines = []
            for paragraph in value.splitlines():
                lines.extend(wrapper.wrap(paragraph))
                lines.append('')
            return "\n".join(lines)

        parser.add_formatter("quote", _render_quote, escape_html=False)
            
        return parser.format(post['message'])
    
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
                
            overviews.append("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
                index + 1,
                post['nntp_subject'],
                post['username'],
                strutil.get_formatted_time(time.localtime(post['post_date'])),
                post['nntp_message_id'],
                reference,
                8192,
                50,
                'Xref: %s %s:%s' % (settings.nntp_hostname, group_name, index)
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
            return self.xn.body_massage(self.xn.posts_by_msgid[id])
        else:
            group = decut(group_name)
            return self.xn.body_massage(self.xn.forums[group]['posts'][int(id) - 1])

    def get_ARTICLE(self, group_name, id):
        return (
            self.get_HEAD(group_name, id),
            self.get_BODY(group_name, id)
        )
