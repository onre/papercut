# Papercut NNTP Server

Papercut is a BSD licensed NNTP server written in 100% pure Python. It is
intended to be extensible to the point where people can develop their own
plug-ins to integrate the NNTP protocol into their applications (I will be
happy to request pull requests for new plugins).

The server is compliant with most of the RFC0977 standards (when they make
sense and are needed) and implements a lot of RFC1036 and RFC2980 extensions to
the NNTP protocol. It was tested against Netscape News, Mozilla News and tin
(under Solaris) and it works properly. This fork was tested against `slrn` and
was found to work.

This version of papercut is a fork of the original version written by Joao
Prado Maia. The original papercut source is no longer maintained, but you can
still find it at <https://github.com/jpm/papercut>. Changes made by this fork
include setuptools packaging, configuration file handling, multiple backend
storage backend support, and various small fixes and tweaks.

A note on supported plugins: the introduction of multi backend support involved
extensive changes to `papercut_nntp.py`. These changes were only tested against
the maildir storage plugin since this is the plugin I primarily use. There is a
fair chance I (partially) broke the other plugins in the course of this. I
probably will not get around to fixing/testing them myself, but I am happy to
accept pull requests both against the code itself or in the form of
documentation detailing what works and what does not.

-- Johannes Grassler <johannes@btw23.de>
