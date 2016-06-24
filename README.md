# Papercut NNTP Server

Papercut is a BSD licencsed NNTP server written in 100% pure Python. It is
intended to be extensible to the point where people can develop their own
plug-ins to integrate the NNTP protocol into their applications (I will be
happy to request pull requests for new plugins).

The server is compliant with most of the RFC0977 standards (when they make
sense and are needed) and implements a lot of RFC1036 and RFC2980 extensions to
the NNTP protocol. It was tested against Netscape News, Mozilla News and tin
(under Solaris) and it works properly.

This version of papercut is a fork of the original version written by Joao
Prado Maia. The original papercut source is no longer maintained, but you can
still find it at <https://github.com/jpm/papercut>. Changes made by this fork
include setuptools packaging, configuration file handling, and various small
fixes and tweaks.

-- Johannes Grassler <johannes@btw23.de>
