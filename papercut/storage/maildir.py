# Copyright (c) 2004 Scott Parish, Joao Prado Maia
# See the LICENSE file for more information.

#
# Maildir backend for papercut
#
# Notes:
#
# Currently the numeric message ids are based off the number of
# files in that group's directy. This means that if you change
# a file name, or delete a file you are going to change ids, which
# in turn is going to confuse nntp clients!
#
# To add a new group:
#     mkdir -p /home/papercut/maildir/my.new.group/{new,cur,tmp}
#

import dircache
from fnmatch import fnmatch
import glob
import os
import mailbox
import rfc822
import socket
import string
import time
import StringIO

from stat import ST_MTIME

import papercut.storage.strutil as strutil
import papercut.settings

settings = papercut.settings.CONF()


def maildir_date_cmp(a, b):
    """compare maildir file names 'a' and 'b' for sort()"""
    a = os.path.basename(a)
    b = os.path.basename(b)
    # Extract portion before first dot (timestamp)
    a = a[: a.find(".")]
    b = b[: b.find(".")]
    # Sanitize timestamp for cases where two files happen to have the same time
    # stamp and had non-digits added to distinguish them (in the case where
    # this problem cropped up there was '_0' and '_1' appended at the end of
    # the time stamp.).
    a = strutil.filterchars(a, string.digits)
    b = strutil.filterchars(b, string.digits)
    return cmp(int(a), int(b))


def new_to_cur(groupdir):
    for f in dircache.listdir(os.path.join(groupdir, 'new')):
        ofp = os.path.join(groupdir, 'new', f)
        nfp = os.path.join(groupdir, 'cur', f + ":2,")
        os.rename(ofp, nfp)


class HeaderCache:
  '''
  Caches the message headers returned by the XOVER command and indexes them by
  file name, message ID and article number.
  '''

  def __init__(self, storage, is_active):
    self.storage = storage
    self.path = storage.maildir_dir
    self.cache = {}        # Message cache
    self.dircache = {}     # Group directory caches
    self.midindex = {}     # Global message ID index
    self.groupindex = {}   # Per group file name index
    self.is_active = is_active

    # Only attempt to read/create caches if caching is enabled
    if is_active:
      for group in dircache.listdir(self.path):
        self.create_cache(group)

  def _idtofile(self, group, articleid):
    '''Converts an group/article ID to a file name'''
    articledir = os.path.join(self.path, group, 'cur')
    articles = self.dircache[group]
    return os.path.join(self.path, articledir, articles[articleid-1])

  def message_byname(self, filename):
    '''
    Retrieve an article's metadata by file name. Falls back to storage
    instance's regular retrieval method if cache is disabled/unavailable.
    '''

  def message_byid(self, group, articleid):
    '''
    Retrieve an article by article ID in its group. Falls back to storage
    instance's regular retrieval method if cache is disabled/unavailable.
    '''

    filename = self._idtofile(group, articleid)

    try:
      return self.cache[filename]
    except IndexError:
      return None


  def message_bymid(self, message_id):
    '''
    Retrieve an article by message ID. Falls back to storage instance's regular
    retrieval method if cache is disabled/unavailable.
    '''

  def create_cache(self, group):
    '''Create an entirely new cache for a group (in memory and on disk)'''
    groupdir = os.path.join(self.path, group)
    new_to_cur(groupdir)
    curdir = os.path.join(groupdir, 'cur')

    self.refresh_dircache(group)

    for message in self.dircache[group]:
      filename = os.path.join(curdir, message)
      ret = self.read_message(filename)

      self.cache[filename] = ret
      mid = ret['headers']['message-id']

      # Add pointers to message data structure
      self.midindex[mid] = filename


  def refresh_dircache(self, group):
    '''
    Refresh internal directory cache for a group. We need to keep this cache
    since the dircache one performs a stat() on the directory for each
    invocation which makes it disastrously slow in a place like message_byid()
    that may be called thousands of times when processing a XOVER.
    '''
    groupdir = os.path.join(self.path, group)
    new_to_cur(groupdir)
    curdir = os.path.join(groupdir, 'cur')

    if not self.dircache.has_key(group):
      self.dircache[group] = {}
    self.dircache[group] = dircache.listdir(curdir)
    self.dircache[group].sort(maildir_date_cmp)


  def read_message(self, filename):
      '''Reads an RFC822 message and creates a data structure containing selected metadata'''
      f = open(filename)

      # Count lines and bytes
      l = f.read().split('\n')
      lines = len(l)
      message_bytes = 0
      for line in l:
        message_bytes += len(line)
      message_bytes += lines # newlines are bytes, too

      f.seek(0)

      m = rfc822.Message(f)

      # Create in-memory data structure with readline() support to minimize I/O
      headers = StringIO.StringIO(''.join(m.headers))
      m = rfc822.Message(headers)

      f.close()

      mid = m.getheader('message-id')

      # Sometimes messages may not have a Message-ID: header. Technically this
      # should not happen. If it is missing anyway, generate a message ID from
      # the file name for messages that lack one.
      if mid is None:
        basename = os.path.basename(filename)
        try:
          hostname = basename.split('.')[2].split(',')[0]
        except IndexError:
          hostname = papercut

        # strip host name from filename
        basename = basename.replace(hostname, '')

        # Remove all nonalphanumeric characters
        basename = strutil.filterchars(basename, string.letters + string.digits)

        mid = basename + '@' + hostname
      else:
        # Remove angle braces and white space from message ID
        mid = mid.strip('<> ')

      metadata = {
        'filename': filename,
        'timestamp': time.time(),
        'lines': lines,
        'bytes': message_bytes,
        'headers': {
           'date': m.getheader('date'),
           'from': m.getheader('from'),
           'message-id': mid,
           'subject': m.getheader('subject'),
           'references': m.getheader('references'),
         }

      }

      # Make sure no headers are None and remove embedded newlines
      for header in metadata['headers']:
        h = metadata['headers']
        if h[header] is None:
          h[header] = ''
        else:
          h[header] = h[header].replace('\n', '')
        metadata['headers'] = h

      return metadata


  def read_cache(self, group):
    '''Reads cache for a group from disk and populates in-memory data structures'''
    # TODO: Implement this if it turns out to be neccessary at some stage.
    open(os.path.join(self.path, group))

  def write_cache(group):
    '''Write in-memory cache for a group to disk'''
    # TODO: Implement this if it turns out to be neccessary at some stage.


class Papercut_Storage:
    """
    Storage backend interface for mbox files
    """
    _proc_post_count = 0

    def __init__(self, group_prefix="papercut.maildir.", header_cache=True):
        self.maildir_dir = settings.maildir_path
        self.group_prefix = group_prefix
        self.cache = HeaderCache(self, header_cache)


    def _get_group_dir(self, group):
        return os.path.join(self.maildir_dir, group)


    def _groupname2group(self, group_name):
        return group_name.replace(self.group_prefix, '')


    def _group2groupname(self, group):
        return self.group_prefix + group


    def _new_to_cur(self, group):
        new_to_cur(self._get_group_dir(group))

    def get_groupname_list(self):
        groups = dircache.listdir(self.maildir_dir)
        return ["papercut.maildir.%s" % k for k in groups]


    def get_group_article_list(self, group):
        self._new_to_cur(group)
        groupdir = self._get_group_dir(group)
        articledir = os.path.join(self._get_group_dir(group), 'cur')
        articles = dircache.listdir(articledir)
        articles.sort(maildir_date_cmp)
        return articles

    
    def get_group_article_count(self, group):
        self._new_to_cur(group)
        articles = dircache.listdir(os.path.join(self.maildir_dir, group))
        return len(articles)

       
    def group_exists(self, group_name):
        groupnames = self.get_groupname_list()
        found = False
        
        for name in groupnames:
            # group names are supposed to be case insensitive
            if string.lower(name) == string.lower(group_name):
                found = True
                break
            
        return found


    def get_first_article(self, group_name):
        return 1


    def get_group_stats(self, group_name):
        total, max, min = self.get_maildir_stats(group_name)
        return (total, min, max, group_name)


    def get_maildir_stats(self, group_name):
        cnt = len(self.get_group_article_list(group_name))
        return cnt, cnt, 1


    def get_message_id(self, msg_num, group_name):
        msg_num = int(msg_num)
        group = self._groupname2group(group_name)
        return '<%s@%s>' % (self.get_group_article_list(group)[msg_num - 1],
                            group_name)


    def get_NEWGROUPS(self, ts, group='%'):
        return None


    # UNTESTED
    def get_NEWNEWS(self, ts, group='*'):
        gpaths = glob.glob(os.path.join(self.maildir_dir, group))
        articles = []
        for gpath in gpaths:
            articles = dircache.listdir(os.path.join(gpath, "cur"))
            group = os.path.basename(gpath)
            group_name = self._group2groupname(group)

            for article in articles:
                apath = os.path.join(gpath, "cur", article)
                if os.path.getmtime(apath) < ts:
                    continue

                articles.append("<%s@%s" % (article, group_name))

        if len(articles) == 0:
            return ''
        else:
            return "\r\n".join(articles)


    def get_GROUP(self, group_name):
        group = self._groupname2group(group_name)
        result = self.get_maildir_stats(group)
        return (result[0], result[2], result[1])


    def get_LIST(self, username=""):
        result = self.get_groupname_list()
        
        if len(result) == 0:
            return ""
        
        else:
            groups = []
            mutable = ('y', 'n')[settings.server_type == 'read-only']
            
            for group_name in result:
                group = self._groupname2group(group_name)
                total, maximum, minimum = self.get_maildir_stats(group)
                groups.append("%s %s %s %s" % (group_name, maximum,
                                               minimum, mutable))
            return "\r\n".join(groups)


    def get_STAT(self, group_name, id):
        # check if the message exists
        id = int(id)
        group = self._groupname2group(group_name)
        
        return id <= self.get_group_article_count(group)

        
    def get_message(self, group_name, id):
        group = self._groupname2group(group_name)
        id = int(id)
        
        try:
            article = self.get_group_article_list(group)[id - 1]
            file = os.path.join(self.maildir_dir, group, "cur", article)
            return rfc822.Message(open(file))
        
        except IndexError:
            return None


    def get_ARTICLE(self, group_name, id):
        msg = self.get_message(group_name, id)
        if not msg:
            return None
        return ("\r\n".join(["%s" % string.strip(k) for k in msg.headers]), msg.fp.read())


    def get_LAST(self, group_name, current_id):
        if current_id <= 1:
            return None
        return current_id - 1


    def get_NEXT(self, group_name, current_id):
        group = self._groupname2group(group_name)
        if current_id >= self.get_group_article_count(group):
            return None
        return current_id + 1
        

    def get_HEAD(self, group_name, id):
        msg = self.get_message(group_name, id)
        headers = []
        headers.append("Path: %s" % (settings.nntp_hostname))
        headers.append("From: %s" % (msg.get('from')))
        headers.append("Newsgroups: %s" % (group_name))
        headers.append("Date: %s" % (msg.get('date')))
        headers.append("Subject: %s" % (msg.get('subject')))
        headers.append("Message-ID: <%s@%s>" % (id, group_name))
        headers.append("Xref: %s %s:%s" % (settings.nntp_hostname,
                                           group_name, id))
        return "\r\n".join(headers)


    def get_BODY(self, group_name, id):
        msg = self.get_message(group_name, id)
        if msg is None:
            return None
        else:
            return strutil.format_body(msg.fp.read())


    def get_XOVER(self, group_name, start_id, end_id='ggg'):
        group = self._groupname2group(group_name)
        start_id = int(start_id)
        if end_id == 'ggg':
            end_id = self.get_group_article_count(group)
        else:
            end_id = int(end_id)
            
        overviews = []

        # Refresh directory cache to get a reasonably current view
        self.cache.refresh_dircache(self._groupname2group(group_name))

        for id in range(start_id, end_id + 1):
            msg = self.cache.message_byid(self._groupname2group(group_name), id)
            
            if msg is None:
                break
            
            author = msg['headers']['from']
            formatted_time = msg['headers']['date']
            message_id = msg['headers']['message-id']
            line_count = msg['lines']
            xref = 'Xref: %s %s:%d' % (settings.nntp_hostname, group_name, id)
            
            subject = msg['headers']['subject']
            reference = msg['headers']['references']
            msg_bytes = msg['bytes']
            # message_number <tab> subject <tab> author <tab> date <tab>
            # message_id <tab> reference <tab> bytes <tab> lines <tab> xref
            
            overviews.append("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % \
                             (id, subject, author,
                              formatted_time, message_id, reference,
                              msg_bytes,
                              line_count, xref))
            
        return "\r\n".join(overviews)


    # UNTESTED
    def get_XPAT(self, group_name, header, pattern, start_id, end_id='ggg'):
        group = self._groupname2group(group_name)
        header = header.upper()
        start_id = int(start_id)
        if end_id == 'ggg':
            end_id = self.get_group_article_count(group)
        else:
            end_id = int(end_id)

        hdrs = []
        for id in range(start_id, end_id + 1):

            if header == 'MESSAGE-ID':
                msg_id = self.get_message_id(id, group_name)
                if fnmatch(msg_id, pattern):
                    hdrs.append('%d %s' % (id, msg_id))
                continue
            elif header == 'XREF':
                xref = '%s %s:%d' % (settings.nntp_hostname, group_name, id)
                if fnmatch(xref, pattern):
                    hdrs.append('%d %s' % (id, xref))
                continue
            
            msg = self.get_message(group_name, id)
            if header == 'BYTES':
                msg.fp.seek(0, 2)
                bytes = msg.fp.tell()
                if fnmatch(str(bytes), pattern):
                    hdrs.append('%d %d' % (id, bytes))
            elif header == 'LINES':
                lines = len(msg.fp.readlines())
                if fnmatch(str(lines), pattern):
                    hdrs.append('%d %d' % (id, lines))
            else:
                hdr = msg.get(header)
                if hdr and fnmatch(hdr, pattern):
                    hdrs.append('%d %s' % (id, hdr))

        if len(hdrs):
            return "\r\n".join(hdrs)
        else:
            return ""


    def get_LISTGROUP(self, group_name):
        ids = range(1, self.get_group_article_count(group) + 1)
        ids = [str(id) for id in ids]
        return "\r\n".join(ids)

    def get_XGTITLE(self, pattern=None):
        # XXX no support for this right now
        return ''


    def get_XHDR(self, group_name, header, style, ranges):
        print group_name, header, style, ranges
        group = self._groupname2group(group_name)
        header = header.upper()

        if style == 'range':
            if len(ranges) == 2:
                range_end = int(ranges[1])
            else:
                range_end = self.get_group_article_count(group)
            ids = range(int(ranges[0]), range_end + 1)
        else:
            ids = (int(ranges[0]))

        hdrs = []
        for id in ids:
            if header == 'MESSAGE-ID':
                hdrs.append('%d %s' % \
                            (id, self.get_message_id(id, group_name)))
                continue
            elif header == 'XREF':
                hdrs.append('%d %s %s:%d' % (id, settings.nntp_hostname,
                                             group_name, id))
                continue

            msg = self.get_message(group_name, id)
            if header == 'BYTES':
                msg.fp.seek(0, 2)
                hdrs.append('%d %d' % (id, msg.fp.tell()))
            elif header == 'LINES':
                hdrs.append('%d %d' % (id, len(msg.fp.readlines())))
            else:
                hdr = msg.get(header)
                if hdr:
                    hdrs.append('%d %s' % (id, hdr))

        if len(hdrs) == 0:
            return ""
        else:
            return "\r\n".join(hdrs)


    def do_POST(self, group_name, body, ip_address, username=''):
        self._proc_post_count += 1
        count = self._proc_post_count

        ts = [int(x) for x in str(time.time()).split(".")]
        file = "%d.M%dP%dQ%d.%s" % (ts[0], ts[1], os.getpid(),
                                    count, socket.gethostname())
        group = self._groupname2group(group_name)
        groupdir = self._get_group_dir(group)
        tfpath = os.path.join(self.maildir_dir, groupdir, "tmp", file)
        nfpath = os.path.join(self.maildir_dir, groupdir, "new", file)
        
        fd = open(tfpath, 'w')
        fd.write(body)
        fd.close

        os.rename(tfpath, nfpath)
        return 1

