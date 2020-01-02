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
import email.message as rfc822
import socket
import string
import time
import io

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
    for f in os.listdir(os.path.join(groupdir, 'new')):
        ofp = os.path.join(groupdir, 'new', f)
        nfp = os.path.join(groupdir, 'cur', f + ":2,")
        try:
          os.rename(ofp, nfp)
        except OSError:
          # This may have been moved already, with dircache not knowing about
          # it, yet.
          print(("DEBUG: ofp = %s: %s" % (ofp, os.path.exists(ofp)) ))
          print(("DEBUG: nfp = %s: %s" % (nfp, os.path.exists(nfp)) ))
          pass


class HeaderCache:
  '''
  Caches the message headers returned by the XOVER command and indexes them by
  file name, message ID and article number.
  '''

  def __init__(self, path):
    self.path = path
    self.cache = {}        # Message cache
    self.dircache = {}     # Group directory caches
    self.midindex = {}     # Global message ID index

    # Only attempt to read/create caches if caching is enabled
    for group in dircache.listdir(self.path):
      self.create_cache(group)

  def _idtofile(self, group, articleid):
    '''Converts an group/article ID to a file name'''
    articledir = os.path.join(self.path, group, 'cur')
    articles = self.dircache[group]
    return os.path.join(self.path, articledir, articles[articleid-1])

  def message_byid(self, group, articleid):
    '''
    Retrieve an article by article ID in its group or None if the article ID is
    not valid.
    '''

    filename = self._idtofile(group, articleid)

    try:
      return self.cache[filename]
    except IndexError:
      return None


  def message_bymid(self, message_id):
    '''
    Return the metadata dict corresponding to a message ID or None if the
    message ID is unknown.
    '''

    try:
      return self.cache[self.midindex[message_id]]
    except KeyError:
      return None


  def create_cache(self, group):
    '''Create an entirely new cache for a group (in memory and on disk)'''
    groupdir = os.path.join(self.path, group)
    new_to_cur(groupdir)
    curdir = os.path.join(groupdir, 'cur')

    self.refresh_dircache(group)

    for message in self.dircache[group]:
      filename = os.path.join(curdir, message)
      self.refresh_article(filename, group, curdir)


  def refresh_dircache(self, group):
    '''
    Refresh internal directory cache for a group. We need to keep this cache
    since the dircache one performs a stat() on the directory for each
    invocation which makes it disastrously slow in a place like message_byid()
    that may be called thousands of times when processing a XOVER.
    '''
    # Sanitize group name to prevent directory traversal
    group.replace('..', '')

    groupdir = os.path.join(self.path, group)

    # Abort if there is no such group
    if not os.path.exists(groupdir):
      return

    new_to_cur(groupdir)
    curdir = os.path.join(groupdir, 'cur')

    if group not in self.dircache:
      self.dircache[group] = []

    # Keep a copy of old cache for cleanup of stale entries
    oldcache = self.dircache[group]

    self.dircache[group] = dircache.listdir(curdir)
    self.dircache[group].sort(maildir_date_cmp)

    # Iterate over both the old and new cache to process new entries and clean
    # up old ones
    for i in range(0, max(len(self.dircache), len(oldcache))):
      # Create new entries
      try:
        filename = os.path.join(curdir, self.dircache[group][i])
        if filename not in self.cache:
          self.refresh_article(filename, group, curdir)
      except IndexError:
        # Either or self.dircache may be shorter, causing IndexError. We can
        # safely ignore these.
        pass

      # Get rid of stale entries
      try:
        filename = os.path.join(curdir, oldcache[i])
        oldmid = self.cache[filename]['headers']['message-id']
        if not os.path.exists(filename):
          self.cache.pop(filename)
          self.midindex.pop(oldmid)
      except IndexError:
        # Either or self.dircache may be shorter, causing IndexError. We can
        # safely ignore these.
        pass


  def refresh_article(self, filename, group, curdir):
    ret = self.read_message(filename, group)
    mid = ret['headers']['message-id']

    self.cache[filename] = ret
    self.midindex[mid] = filename

  def read_message(self, filename, group):
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
      headers = io.StringIO(''.join(m.headers))
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

      metadata = {
        'filename': filename,
        'timestamp': time.time(),
        'lines': lines,
        'bytes': message_bytes,
        'group': group,
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

    # Capabilities of this storage backend
    capabilities = {
      'message-id': True, # Regular message IDs supported (For ARTICLE, HEAD, STAT)
      }

    def __init__(self, group_prefix="papercut.maildir.", local_settings={}):
        self.maildir_path = settings.maildir_path
        self.group_prefix = group_prefix

        # Override global settings with hierarchy specific ones
        self.__dict__.update(local_settings)

        self.cache = HeaderCache(self.maildir_path)

    def _get_group_dir(self, group):
        return os.path.join(self.maildir_path, group)


    def _groupname2group(self, group_name):
        return group_name.replace(self.group_prefix, '', 1).strip('.')


    def _group2groupname(self, group):
        return self.group_prefix + group


    def _new_to_cur(self, group):
        new_to_cur(self._get_group_dir(group))
        self.cache.refresh_dircache(group)

    def get_groupname_list(self):
        groups = dircache.listdir(self.maildir_path)
        group_list = []
        for group in groups:
          group_list.append("%s.%s" % (self.group_prefix, group))
        return group_list


    def get_group_article_list(self, group):
        self._new_to_cur(group)
        self.cache.refresh_dircache(group)
        try:
          articles = self.cache.dircache[group]
          return articles
        except KeyError:
          return []

    
    def get_group_article_count(self, group):
        self._new_to_cur(group)
        self.cache.refresh_dircache(group)
        articles = self.cache.dircache[group]
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
        group = self._groupname2group(group_name)
        total, max, min = self.get_maildir_stats(group)
        return (total, min, max, group)


    def get_maildir_stats(self, group_name):
        cnt = len(self.get_group_article_list(group_name))
        return cnt, cnt, 1


    def get_article_number(self, mid):
        '''
        Converts Message ID to group/article number tuple
        '''

        msg = self.cache.message_bymid(mid)

        if not msg:
          return [None, -1]

        group = msg['group']
        article = os.path.basename(msg['filename'])
        self.cache.refresh_dircache(group)
        try:
          article_id = self.cache.dircache[group].index(article)
          return [group, article_id]
        except ValueError:
          # Article has been deleted, but we can at least return the group it
          # used to be in.
          return [group, -1]


    def get_message_id(self, msg_num, group_name):
        '''
        Converts group/article number to message ID
        '''
        try:
          msg_num = int(msg_num)
        except ValueError:
          # Non-numeric, so it's probably a message ID already.
          return msg_num
        group = self._groupname2group(group_name)
        msg = self.cache.message_byid(group, msg_num)
        try:
          return msg['headers']['message-id']
        except KeyError:
          return None


    def get_NEWGROUPS(self, ts, group='%'):
        return None


    def get_NEWNEWS(self, ts, group='*'):
        groups = []
        for token in group.split(','):
          # This will still break some wildcards (e.g. *foo.bar
          # if the prefix is foo.bar.baz) but it will at least work for
          # full group names.
          token = self._groupname2group(token)
          glob_target = os.path.join(self.maildir_path, token)
          groups.extend(glob.glob(glob_target))

        # Nonexistent groups and/or patterns that do not match
        if len(groups) == 0:
          return ''

        mids = []
        res = []
        for group in groups:
            groupdir = os.path.join(self.maildir_path, group, "cur")

            # Make sure we have an up-to-date cache for retrieving message IDs
            self.cache.refresh_dircache(group)

            articles = self.cache.dircache[group]

            for article in articles:
                apath = os.path.join(groupdir, article)
                if os.path.getmtime(apath) < ts:
                    continue
                try:
                  res.append(self.cache.cache[apath]['headers']['message-id'])
                except KeyError:
                  pass

        if len(res) == 0:
            return ''
        else:
            return "\r\n".join(res)


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
        try:
          id = int(id)
          group = self._groupname2group(group_name)
          return id <= self.get_group_article_count(group)
        except ValueError:
          # Treat non-numeric ID as Message-ID
          msg = self.cache.message_bymid(id)
          if msg:
            group = msg['group']
            self.cache.refresh_dircache(group)
            try:
              article_id = self.cache.dircache[group].index(os.path.basename(msg['filename']))
            except ValueError:
              return False
            return article_id <= len(self.cache.dircache[group])
          else:
            return False



    def get_message(self, group_name, id):
        group = self._groupname2group(group_name)

        filename = ''

        try:
          id = int(id)
          try:
            article = self.get_group_article_list(group)[id - 1]
            filename = os.path.join(self.maildir_path, group, "cur", article)
          except IndexError:
              return None
        except ValueError:
          # Treat non-numeric ID as Message-ID
          try:
            filename = self.cache.message_bymid(id.strip())['filename']
          except TypeError:
            # message_bymid() returned None
            return None

        try:
          return rfc822.Message(open(filename))
        except IOError:
          return None
        


    def get_ARTICLE(self, group_name, id):
        msg = self.get_message(group_name, id)
        if not msg:
            return None
        return ("\r\n".join(["%s" % string.strip(k) for k in msg.headers]), msg.fp.read())

    def _sanitize_id(self, article_id):
        try:
          article_id = int(article_id)
          return article_id
        except ValueError:
          # non-numeric ID is garbage
          return None

    def get_LAST(self, group_name, current_id):
        current_id = self._sanitize_id(current_id)
        if not current_id:
          return None
        if current_id <= 1:
            return None
        return current_id - 1


    def get_NEXT(self, group_name, current_id):
        current_id = self._sanitize_id(current_id)
        if not current_id:
          return None
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
        self.cache.refresh_dircache(group)

        # Adjust end ID downwards if it is out of range
        if end_id >= len(self.cache.dircache[group]):
          end_id = len(self.cache.dircache[group]) - 1

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
        group = self._groupname2group(group_name)
        self.cache.refresh_dircache(group)
        ids = list(range(1, len(self.cache.dircache[group]) + 1))
        ids = [str(id) for id in ids]
        return "\r\n".join(ids)

    def get_XGTITLE(self, pattern=None):
        # XXX no support for this right now
        return ''


    def get_XHDR(self, group_name, header, style, ranges):
        group = self._groupname2group(group_name)
        header = header.upper()

        if style == 'range':
            if len(ranges) == 2:
                range_end = int(ranges[1])
            else:
                range_end = self.get_group_article_count(group)
            ids = list(range(int(ranges[0]), range_end + 1))
        else:
            ids = [ranges]

        hdrs = []
        for id in ids:
            mid = self.get_message_id(id, group_name)
            meta = self.cache.message_bymid(mid)

            if meta is None:
              # Message ID unknown
              return ""

            msg = self.get_message(group_name, id)

            if header == 'MESSAGE-ID':
                hdrs.append('%d %s' % \
                            (id, self.get_message_id(id, group_name)))
                continue
            elif header == 'XREF':
                hdrs.append('%d %s %s:%d' % (id, settings.nntp_hostname,
                                             group_name, id))
                continue

            if header == 'BYTES':
                hdrs.append('%d %d' % (id, meta['bytes']))
            elif header == 'LINES':
                hdrs.append('%d %d' % (id,  meta['lines']))
            else:
                hdr = msg.get(header)
                if hdr:
                    hdrs.append('%s %s' % (id, hdr))

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
        tfpath = os.path.join(self.maildir_path, groupdir, "tmp", file)
        nfpath = os.path.join(self.maildir_path, groupdir, "new", file)
        
        fd = open(tfpath, 'w')
        fd.write(body)
        fd.close

        os.rename(tfpath, nfpath)
        self.cache.refresh_dircache(group)
        return 1

