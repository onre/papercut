import bbcode
import re
import pprint
import textwrap

pp = pprint.PrettyPrinter(indent=2)

class Body_Massager:
    def __init__(self):
        pass

    def massage(self, post):
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
        parser.add_simple_formatter('color', '%(value)s', escape_html=False)
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

        def _render_table(name, value, options, parent, context):
            max_col_lengths = {}
            header_rows = []

            bb_rows = re.findall(r'\[tr\].*?\[/tr\]', value, re.IGNORECASE)

            data = {}

            row_index = 0

            # first, analyze what we have
            for rownum,row in enumerate(bb_rows):
                data[row_index] = {}
                more_rows = 1
                # there's more than zero THs, so it's a header row, right?
                if re.match(r'.*\[th\]', row, re.IGNORECASE):
                    header_rows.append(rownum)
                bb_cols = re.findall(r'\[t[hd]\].*?\[/t[hd]\]', row, re.IGNORECASE)
                for colnum,col in enumerate(bb_cols):
                    match = re.match(r'.*?\](.*)\[', col)
                    if match:
                        coldata = match.group(1)
                    else:
                        coldata = ''
                    # the cell may have newlines
                    coldata_rows = re.split('\\r', coldata)
                    coldata_rowcnt = len(coldata_rows)
                    
                    if coldata_rowcnt > 1: # cell has newlines
                        for coldata_row in coldata_rows:
                             # non-first line may be the longest
                            if colnum not in max_col_lengths or len(coldata_row) > max_col_lengths[colnum]:
                                max_col_lengths[colnum] = len(coldata_row)

                            # the table needs more rows than it has for data placing to work
                            if coldata_rowcnt >= more_rows:
                                more_rows = coldata_rowcnt
                                while len(data) < (row_index + more_rows):
                                    data[len(data)] = {}

                            # bombs away
                            for colrownum,coldata_content in enumerate(coldata_rows):
                                data[row_index+colrownum][colnum] = coldata_content
                    else:
                        if colnum not in max_col_lengths or len(coldata_rows[0]) > max_col_lengths[colnum]:
                            max_col_lengths[colnum] = len(coldata_rows[0])
                        data[row_index][colnum] = coldata_rows[0]

                row_index += more_rows

            horizontal_separator = '+'
            for colnum,length in max_col_lengths.items():
                 horizontal_separator += '%s+' % (length * '=')
            horizontal_separator += "\n"

            ret = "\n" + horizontal_separator

            # let's see what we've got
            for rownum,row in data.items():
                # iterate by max lengths because we're using dicts instead
                # of lists so a continuation row for a multi-line cell might
                # not have all elements in place
                for colnum,maxlen in max_col_lengths.items():
                    if colnum in row:
                        ret += '|%s' % row[colnum].rjust(maxlen, ' ')
                    else:
                        ret += '|%s' % (maxlen * ' ')
                        
                ret += '|'
                ret += "\n"
                if rownum in header_rows:
                    ret += horizontal_separator

            ret += horizontal_separator
                
            return ret

        parser.add_formatter("table", _render_table, escape_html=False)
        
        return parser.format(post['message'])
    
