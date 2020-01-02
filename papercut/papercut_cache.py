# Copyright (c) 2002 Joao Prado Maia. See the LICENSE file for more information.

import binascii
import md5
import time
import os
import pickle
import papercut.portable_locker
import papercut.settings

settings = papercut.settings.CONF()


# methods that need to be cached
cache_methods = ('get_XHDR', 'get_XGTITLE', 'get_LISTGROUP',
                 'get_XPAT', 'get_XOVER', 'get_BODY',
                 'get_HEAD', 'get_ARTICLE', 'get_STAT',
                 'get_LIST')


class CallableWrapper:
    name = None
    thecallable = None
    cacheable_methods = ()

    def __init__(self, name, thecallable, cacheable_methods):
        self.name = name
        self.thecallable = thecallable
        self.cacheable_methods = cacheable_methods

    def __call__(self, *args, **kwds):
        if self.name not in self.cacheable_methods:
            return self.thecallable(*args, **kwds)
        else:
            filename = self._get_filename(*args, **kwds)
            if os.path.exists(filename):
                # check the expiration
                expire, result = self._get_cached_result(filename)
                diff = time.time() - expire
                if diff > settings.nntp_cache_expire:
                    # remove the file and run the method again
                    return self._save_result(filename, *args, **kwds)
                else:
                    return result
            else:
                return self._save_result(filename, *args, **kwds) 

    def _get_cached_result(self, filename):
        inf = open(filename, 'rb')
        # get a lock on the file
        portable_locker.lock(inf, portable_locker.LOCK_SH)
        expire = pickle.load(inf)
        result = pickle.load(inf)
        # release the lock
        portable_locker.unlock(inf)
        inf.close()
        return (expire, result)

    def _save_result(self, filename, *args, **kwds):
        result = self.thecallable(*args, **kwds)
        # save the serialized result in the file
        outf = open(filename, 'w')
        # file write lock
        portable_locker.lock(outf, portable_locker.LOCK_EX)
        pickle.dump(time.time(), outf)
        pickle.dump(result, outf)
        # release the lock
        portable_locker.unlock(outf)
        outf.close()
        return result

    def _get_filename(self, *args, **kwds):
        arguments = '%s%s%s' % (self.name, args, kwds)
        return '%s%s' % (settings.nntp_cache_path, binascii.hexlify(md5.new(arguments).digest()))


class Cache:
    backend = None
    cacheable_methods = ()

    def __init__(self, storage_handle, cacheable_methods):
        self.backend = storage_handle.Papercut_Storage()
        self.cacheable_methods = cacheable_methods

    def __getattr__(self, name):
        result = getattr(self.backend, name)
        if callable(result):
            result = CallableWrapper(name, result, self.cacheable_methods)
        return result

