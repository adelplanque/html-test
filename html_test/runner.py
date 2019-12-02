# -*- coding: utf-8 -*-

from six.moves import cStringIO as StringIO
import codecs
import collections
import datetime
import imghdr
import json
import logging
import os
import pkg_resources
import six
import socket
import sys
import unittest
import uuid

from jinja2 import Template
from pygments import highlight
from pygments import formatters
from pygments import lexers


# Default stdout
stdout = sys.stdout


status_dict = {
    'success': 'Success',
    'fail': 'Fail',
    'error': 'Error',
    'skip': 'Skip',
}


pygments_css = formatters.HtmlFormatter().get_style_defs()


class Singleton(type):

    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = \
                super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


@six.add_metaclass(Singleton)
class Config(object):

    @property
    def dest_path(self):
        if not hasattr(self, '_dest_path'):
            self.dest_path = 'html'
        return self._dest_path

    @dest_path.setter
    def dest_path(self, x):
        if not os.path.exists(x):
            os.makedirs(x)
        self._dest_path = x

    def get_context(self):
        return {
            'hostname': socket.gethostname(),
            'date': datetime.datetime.now(),
            'links': getattr(self, 'links', []),
        }


class TestFormatter(logging.Formatter):
    """
    Format log entry as json (logger, level, message)
    """

    def format(self, record):
        if record.args:
            msg = record.msg % record.args
        else:
            msg = record.msg
        return json.dumps((record.name, record.levelname, msg))


class ImageResult(object):

    def __init__(self, result, expected=None):
        self.result = self.write_img(result)
        self.expected = self.write_img(expected)

    def write_img(self, data):
        """
        Save image and return filename.
        """
        if data is None:
            return
        img_type = imghdr.what(None, data)
        if not img_type:
            try:
                with open(data, 'rb') as infile:
                    data = infile.read()
            except:
                return
            img_type = imghdr.what(None, data)
        if not img_type:
            return
        filename = 'img-%s.%s' % (str(uuid.uuid4()), img_type)
        destpath = os.path.join(Config().dest_path, 'img')
        if not os.path.exists(destpath):
            os.makedirs(destpath)
        with open(os.path.join(destpath, filename), 'wb') as outfile:
            outfile.write(data)
        return os.path.join('img', filename)

    def to_dict(self):
        return {
            'result': self.result,
            'expected': self.expected,
        }


class FileResult(object):
    """
    File to be add to test report.
    """

    def __init__(self, **kwargs):
        path = os.path.join(Config().dest_path, 'data')
        if not os.path.exists(path):
            os.makedirs(path)
        self.filename = 'file-%s' % str(uuid.uuid4())
        self.filepath = os.path.join(path, self.filename)
        content = kwargs.get('content', "")
        with open(self.filepath, 'w') as outfile:
            outfile.write(content)
        self.title = kwargs.get('title', self.filename)

    def to_dict(self):
        return {
            'title': self.title,
            'filename': os.path.join('data', self.filename),
        }


def safe_unicode(s):
    if not s:
        return six.u('')
    try:
        if six.PY2:
            if isinstance(s, unicode):
                return s
            if isinstance(s, str):
                return unicode(s, 'utf-8', errors='replace')
            return s
        else:
            return str(s)
    except Exception as e:
        return six.u(e)


class MethodResult(object):
    """
    Report for one test.
    """

    def __init__(self, status, test, logs):
        if hasattr(test, 'test'):
            # We are using nosetest. `test` is a nose wrapper.
            test = test.test
        self.uid = str(uuid.uuid4())
        self.status = status
        self.status_title = status_dict.get(status) or 'Unknown'
        test_class = test.__class__
        self.name = "%s.%s.%s" % (
            test_class.__module__, test_class.__name__,
            getattr(test, '_testMethodName', 'test')
        )
        images = []
        for img in getattr(test, '_images', ()):
            img = ImageResult(img.get('result'), img.get('expected'))
            images.append(img.to_dict())
        files = []
        for f in getattr(test, '_files', ()):
            files.append(FileResult(**f).to_dict())

        context = {
            'name': self.name,
            'status': status,
            'status_title': self.status_title,
            'doc_class': safe_unicode(test_class.__doc__),
            'doc_test': safe_unicode(getattr(test, '_testMethodDoc', '')),
            'console': safe_unicode(logs.get('console')),
            'logs': logs.get('log'),
            'tracebacks': logs.get('tracebacks'),
            'reason': safe_unicode(logs.get('reason')),
            'images': images,
            'files': files,
            'pygments_css': pygments_css,
        }
        context.update(Config().get_context())
        template = Template(
            pkg_resources.resource_string(
                'html_test',
                os.path.join('templates', 'test-case.html')).decode('utf-8')
        )

        self.url = self.name + '.html'
        filename = os.path.join(Config().dest_path, self.url)
        with codecs.open(filename, 'w', encoding="utf-8") as outfile:
            outfile.write(template.render(context))


class TbFrame(object):
    """
    Expose one frame of a traceback to jinja2.
    """

    CodeLine = collections.namedtuple(
        'CodeLine', ('lineno', 'code', 'highlight', 'extended'))
    VarLine = collections.namedtuple('VarLine', ('name', 'value'))

    def __init__(self, filename, lineno, name, vars_dict):
        self.filename = filename
        self.lineno = lineno
        self.name = name
        self.vars_dict = vars_dict
        self.id = str(uuid.uuid4())

    @property
    def code_fragment(self):
        fragment_length = 50
        start = max(1, self.lineno - fragment_length)
        stop = self.lineno + fragment_length
        lineno = 1
        lexer = lexers.Python3Lexer(stripnl=False)
        formatter = formatters.HtmlFormatter(full=False, linenos=False)

        try:
            with codecs.open(self.filename, 'r', encoding='utf-8') as infile:
                for line in infile:
                    if lineno >= start:
                        frag = formatter._highlight_lines(formatter._format_lines(
                            lexer.get_tokens(line)))
                        yield self.CodeLine(
                            lineno, six.next(frag)[1].rstrip(),
                            lineno == self.lineno,
                            lineno <= self.lineno - 2 or lineno >= self.lineno + 2
                        )
                    if lineno >= stop:
                        break
                    lineno += 1
        except IOError:
            return

    @property
    def loc_vars(self):
        lexer_js = lexers.JavascriptLexer()
        lexer_text = lexers.TextLexer()
        formatter = formatters.HtmlFormatter(full=False, linenos=False)
        for name, value in sorted(self.vars_dict.items()):
            try:
                value = json.dumps(value, indent=4)
                value = highlight(value, lexer_js, formatter)
            except Exception:
                try:
                    value = six.u(repr(value))
                except Exception as e:
                    try:
                        value = six.u(e)
                    except Exception:
                        value = ''
                value = highlight(value, lexer_text, formatter)
            yield self.VarLine(name, value)


class Traceback(object):
    """
    Expose one traceback to jinja2.
    """

    def __init__(self, tb):
        self.tb = tb

    def __iter__(self):
        tb = self.tb
        while tb:
            frame = tb.tb_frame
            lineno = tb.tb_lineno
            co = frame.f_code
            filename = co.co_filename
            name = co.co_name
            loc_vars = frame.f_locals
            yield TbFrame(filename, lineno, name, loc_vars)
            tb = tb.tb_next


class TracebackHandler(list):
    """
    Expose traceback list to jinja2.
    """

    def __init__(self, exc_info):
        etype, evalue, tb = exc_info
        if six.PY2:
            self.append(Traceback(tb))
        else:
            while evalue:
                self.append(Traceback(evalue.__traceback__))
                evalue = evalue.__context__
        self.reverse()


class TestIndex(dict):

    def __init__(self, name=None, status=None, url=None):
        self._name = name
        self._status = status
        self._url = url

    def append(self, name, status, url):
        toks = name.split('.', 1)
        if len(toks) == 1:
            self[name] = TestIndex(name, status, url)
        else:
            root, path = toks
            if root not in self:
                self[root] = TestIndex(root)
            self[root].append(path, status, url)

    def get_status(self):
        if self._status is None:
            status_count = {
                'success': 0,
                'fail': 0,
                'error': 0,
                'skip': 0,
            }
            for child in self.values():
                status_count[child.get_status()] += 1
            for name in ('error', 'fail', 'skip', 'success'):
                if status_count[name]:
                    self._status = name
                    break
        return self._status

    def as_json(self):
        return {
            'title': self._name,
            'url': self._url,
            'status': self.get_status(),
            'childs': [x[1].as_json() for x in sorted(self.items())]
        }


class ResultMixIn(object):
    """
    Shared code of HtmlTestResult with nose plugin.
    """

    def __init__(self, *args, **kwargs):
        super(ResultMixIn, self).__init__(*args, **kwargs)
        self._results = []

    def add_result_method(self, status, test, exc_info=None, reason=None):
        """
        Add test result.
        """
        logs = {}
        if exc_info is not None:
            logs['tracebacks'] = TracebackHandler(exc_info)
        logs['reason'] = reason
        try:
            console = self._buffer_console.getvalue()
        except AttributeError:
            console = None
        logs['console'] = console
        try:
            log = self._buffer_log.getvalue()
            log = [json.loads(x) for x in log.splitlines()]
        except AttributeError:
            log = None
        if log:
            logs['log'] = log
        result = MethodResult(status, test, logs)
        stdout.write(result.status_title + "\n")
        self._results.append(result)

    def startTest(self, test):
        stdout.write(
            "Run test: %s.%s... " %
            (test.__class__.__name__, test._testMethodName))
        # Capture stdout and stderr.
        self._old_stderr = sys.stderr
        self._old_stdout = sys.stdout
        self._buffer_console = StringIO()
        sys.stdout = sys.stderr = self._buffer_console

        # Capture logs
        self._old_handlers = []
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
            self._old_handlers.append(handler)
        self._buffer_log = StringIO()
        handler = logging.StreamHandler(stream=self._buffer_log)
        handler.setFormatter(TestFormatter())
        handler.setLevel(logging.DEBUG)
        logging.root.addHandler(handler)

    def stopTest(self, test):
        # Restore stdout and stderr.
        sys.stdout = self._old_stdout
        sys.stderr = self._old_stderr
        self._buffer_console.close()
        self._buffer_console = None
        # Restore logs
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        for handler in self._old_handlers:
            logging.root.addHandler(handler)
        self._buffer_log.close()
        self._buffer_log = None

    def get_index(self):
        index = TestIndex()
        for result in self._results:
            index.append(result.name, result.status, result.url)
        return index


class HtmlTestResult(ResultMixIn, unittest.TestResult):

    def addError(self, test, err):
        super(HtmlTestResult, self).addError(test, err)
        self.add_result_method('error', test, exc_info=err)

    def addFailure(self, test, err):
        super(HtmlTestResult, self).addFailure(test, err)
        self.add_result_method('fail', test, exc_info=err)

    def addSuccess(self, test):
        super(HtmlTestResult, self).addSuccess(test)
        self.add_result_method('success', test)

    def addSkip(self, test, reason):
        super(HtmlTestResult, self).addSkip(test, reason)
        self.add_result_method('skip', test, reason=reason)

    def addExpectedFailure(self, test, err):
        super(HtmlTestResult, self).addExpectedFailure(self, test, err)
        self.add_result_method('fail', test, exc_info=err)

    def addUnexpectedSuccess(self, test):
        super(HtmlTestResult, self).addUnexpectedSuccess(self, test)
        self.add_result_method('fail', test)


class HtmlTestRunner(object):
    """
    Alternative to standard unittest TextTestRunner rendering test with full
    logs, image, attached file, in a nice html way.

    Can be use:
    * standalone, just replace `python -m unittest` with `html-test`
    """

    def __init__(self, stream=sys.stderr, descriptions=True, verbosity=1,
                 failfast=False, buffer=False, resultclass=None):
        self.stream = stream
        self.descriptions = descriptions
        self.verbosity = verbosity
        self.failfast = failfast
        self.buffer = buffer
        self.resultclass = resultclass
        self.start_time = datetime.datetime.now()

    def run(self, test):
        result = HtmlTestResult(self.verbosity)
        test(result)
        self.stop_time = datetime.datetime.now()
        Report(result).make_report()
        print('Time Elapsed: %s' % (self.stop_time - self.start_time))
        return result


class Report(object):

    def __init__(self, result):
        self.result = result

    def make_report(self):
        """
        Create html report for the tests results.
        """
        # Create index data
        template = Template("var index = {{data|safe}};")
        index_js = os.path.join(Config().dest_path, 'index.js')
        with open(index_js, 'w') as outfile:
            outfile.write(template.render({
                'data': json.dumps(self.result.get_index().as_json(), indent=4)
            }))
        # Create index page
        template = Template(
            pkg_resources.resource_string(
                'html_test',
                os.path.join('templates', 'test-case.html')).decode('utf-8')
        )
        filename = os.path.join(Config().dest_path, 'index.html')
        with codecs.open(filename, 'w', encoding="utf-8") as outfile:
            outfile.write(template.render(Config().get_context()))