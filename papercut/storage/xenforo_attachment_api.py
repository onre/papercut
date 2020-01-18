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
    group_name = 'binaries.test'
    
    def __init__(self, *args, **kwargs):
        self.api_key = settings.xenforo_api_key
        self.api_url = settings.xenforo_api_url
        self.spool = settings.xenforo_api_spool
        self.xn = XenforoCommon(self.api_key, self.api_url, self.spool)
        
    def group_exists(self, group_name):
        if group_name == self.group_name:
            return True
        else:
            return False

    def get_LIST(self, username=""):
        attcnt = len(self.xn.attachments)
        return "\r\n%s %s %s y\r\n" % (self.group_name, attcnt, attcnt)

    def get_GROUP(self, group_name):
        attcnt = len(self.xn.attachments)
        return (attcnt, 1, attcnt)

    def get_XOVER(self, group_name, start_id, end_id=100):
        if group_name != self.group_name:
            return None

        overviews = []
        
        for index, attachment in enumerate(self.xn.attachments[int(start_id) - 1:int(end_id)]):
            print(attachment)
            msg_num = index + 1
            overviews.append("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
                msg_num,
                attachment['filename'],
                'N/A',
                strutil.get_formatted_time(time.localtime(attachment['attach_date'])),
                '<attachment.%s@forums.sgi.sh>' % attachment['attachment_id'],
                '',
                8192,
                50,
                'Xref: %s %s:%s' % (settings.nntp_hostname, group_name, msg_num)
            ))

        return "\r\n".join(overviews)
            
