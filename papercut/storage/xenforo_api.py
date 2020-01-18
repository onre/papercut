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

from .xenforo_common import XenforoCommon

settings = papercut.settings.CONF()
pp = pprint.PrettyPrinter(indent=2)

def slugify(str):
    return re.sub('[^a-z0-9]+', '-', str.lower())

    
def decut(group_name):
    return re.sub('^sgug\.', '', group_name)
    
class Papercut_Storage:
    def __init__(self, *args, **kwargs):
        self.api_key = settings.xenforo_api_key
        self.api_url = settings.xenforo_api_url
        self.spool = settings.xenforo_api_spool
        self.xn = XenforoCommon(self.api_key, self.api_url, self.spool)

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

    def get_group_stats(self, group_name):
        group = decut(group_name)
        msgcount = len(self.xn.forums[group]['posts'])
        return (msgcount, 1, msgcount, group_name)
    
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

    def get_LISTGROUP(self, group_name):
        group = decut(group_name)
        ret = []
        for val in range(1, len(self.xn.forums[group]['posts']) + 1):
            ret.append(str(val))
        return "\r\n".join(ret)
    
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
            return self.xn.create_usenet_headers(self.xn.posts_by_msgid[id], id)
        else:
            group = decut(group_name)
            return self.xn.create_usenet_headers(self.xn.forums[group]['posts'][int(id) - 1], id)

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

    def get_XGTITLE(self, pattern=None):
        ret = []
        for group in self.xn.forums:
            ret.append('sgug.%s %s' % (group, self.xn.forums[group]['description']))
        return "\r\n".join(ret)

