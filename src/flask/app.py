# -*- coding: utf-8 -*-
"""
    flask.app
    ~~~~~~~~

    This module implements the central WSGI application object.

    :copyright: 2010 Pallets
    :license: BSD-3-Clause
"""
import os
import sys
import typing as t
from datetime import timedelta

from . import cli
from .config import Config
from .ctx import AppContext, RequestContext
from .globals import _app_ctx_stack, _request_ctx_stack
from .helpers import (
    _endpoint_from_view_func,
    find_package,
    get_debug_flag,
    get_flashed_messages,
    url_for,
)
from .json import JSONDecoder as _JSONDecoder
from .json import JSONEncoder as _JSONEncoder
from .logging import create_logger
from .sessions import SecureCookieSessionInterface
from .signals import appcontext_tearing_down, request_finished, request_started
from .templating import DispatchingJinjaLoader, Environment
from .typing import AfterRequestCallable, BeforeRequestCallable, TeardownCallable
from .wrappers import Request, Response

if t.TYPE_CHECKING:
    from .blueprints import Blueprint

F = t.TypeVar("F", bound=t.Callable[..., t.Any])
T_route = t.TypeVar("T_route", bound=t.Callable[..., t.Any])
T_after_request = t.TypeVar("T_after_request", bound=AfterRequestCallable)
T_before_request = t.TypeVar("T_before_request", bound=BeforeRequestCallable)
T_teardown = t.TypeVar("T_teardown", bound=TeardownCallable)
T_error_handler = t.TypeVar(
    "T_error_handler", bound=t.Callable[[Exception], t.Any]
)
T_shell_context_processor = t.TypeVar(
    "T_shell_context_processor", bound=t.Callable[[], t.Dict[str, t.Any]]
)
T_template_filter = t.TypeVar("T_template_filter", bound=t.Callable[..., t.Any])
T_template_global = t.TypeVar("T_template_global", bound=t.Callable[..., t.Any])
T_template_test = t.TypeVar("T_template_test", bound=t.Callable[..., t.Any])


def setupmethod(f: F) -> F:
    """Wraps a method so that it performs a check in debug mode that the
    setup was not already called.
    """

    def wrapper_func(self, *args, **kwargs):  # type: ignore
        if self.debug and self._got_first_request:
            raise AssertionError(
                "A setup function was called after the first request was handled."
                " This usually indicates a bug in the application where a module"
                " was not imported and decorators or other functionality was"
                " called too late.\nTo fix this make sure to import all your view"
                " modules, database functions and similar things in one place"
                " either before the first request or before the first request"
                " handling function."
            )
        return f(self, *args, **kwargs)

    return t.cast(F, wrapper_func)


class Flask:
    """The flask object implements a WSGI application and acts as the central
    object.  It is passed the name of the module or package of the
    application.  Once it is created it will act as a central registry for
    the view functions, the URL rules, template configuration and much more.

    The name of the package is used to resolve resources from inside the
    package or the folder the module is contained in depending on if the
    package parameter resolves to an actual python package (a folder with
    an ``__init__.py`` file inside) or a standard module (just a ``.py`` file).

    For more information about resource loading, see :func:`open_resource`.

    Usually you create a :class:`Flask` instance in your main module or
    in the ``__init__.py`` file of your package like this::

        from flask import Flask
        app = Flask(__name__)

    .. admonition:: About the First Parameter

        The idea of the first parameter is to give Flask an idea of what
        belongs to your application.  This name is used to find resources
        on the filesystem, can be used by extensions to improve debugging
        information and a lot more.

        So it's important what you provide there.  If you are using a single
        module, `__name__` is always the correct value.  If you however are
        using a package, it's usually recommended to hardcode the name of
        your package there.

        For example if your application is defined in ``yourapplication/app.py``
        you should create it with one of the two values::

            app = Flask('yourapplication')
            app = Flask(__name__.split('.')[0])

        Why is that?  The application will work even with `__name__`, thanks
        to how resources are looked up.  However it will make debugging more
        painful.  Certain extensions can make assumptions based on the
        import name of your application.

    .. versionchanged:: 1.0
        The ``static_url_path``, ``static_folder``, and ``template_folder``
        parameters were added.

    .. versionchanged:: 0.8
        Added support for ``instance_relative_config``.

    .. versionchanged:: 0.7
        The ``static_url_path`` parameter was added.

    .. versionchanged:: 0.5
        Added ``instance_path``.
    """

    #: The class that is used for request objects.  See :class:`~flask.Request`
    #: for more information.
    request_class = Request  # type: t.Type[Request]

    #: The class that is used for response objects.  See
    #: :class:`~flask.Response` for more information.
    response_class = Response  # type: t.Type[Response]

    #: The class that is used for the ``json`` functions.  See
    #: :class:`~flask.json.JSONDecoder` for more information.
    json_decoder = _JSONDecoder  # type: t.Type[_JSONDecoder]

    #: The class that is used for the ``json`` functions.  See
    #: :class:`~flask.json.JSONEncoder` for more information.
    json_encoder = _JSONEncoder  # type: t.Type[_JSONEncoder]

    #: The class that is used for the ``jinja_environment`` attribute.
    #: See :class:`~flask.templating.Environment` for more information.
    jinja_environment = Environment  # type: t.Type[Environment]

    #: The class that is used for the ``session_interface`` attribute.
    #: See :class:`~flask.sessions.SecureCookieSessionInterface` for more
    #: information.
    session_interface = SecureCookieSessionInterface()  # type: t.Any

    def __init__(
        self,
        import_name: str,
        static_url_path: t.Optional[str] = None,
        static_folder: t.Optional[str] = "static",
        static_host: t.Optional[str] = None,
        host_matching: bool = False,
        subdomain_matching: bool = False,
        template_folder: t.Optional[str] = "templates",
        instance_path: t.Optional[str] = None,
        instance_relative_config: bool = False,
        root_path: t.Optional[str] = None,
    ):
        _PackageBoundObject.__init__(
            self,
            import_name,
            template_folder=template_folder,
            root_path=root_path,
        )

        if static_url_path is not None:
            self.static_url_path = static_url_path

        if static_folder is not None:
            self.static_folder = static_folder  # type: ignore

        if instance_path is None:
            instance_path = self.auto_find_instance_path()
        elif not os.path.isabs(instance_path):
            raise ValueError(
                "If an instance path is provided it must be absolute."
                f" A relative path was given instead: {instance_path}"
            )

        self.instance_path = instance_path

        self.config = self.make_config(instance_relative_config)

        self._view_functions = {}  # type: t.Dict[str, t.Callable]
        self._error_handlers = {}  # type: t.Dict[t.Union[int, t.Type[Exception]], t.Callable]
        self.url_map = Map()
        self.url_map.host_matching = host_matching
        self.url_map.subdomain_matching = subdomain_matching
        self.url_map.converters["default"] = UnicodeConverter
        self.url_map.converters["string"] = UnicodeConverter
        self.url_map.converters["any"] = AnyConverter
        self.url_map.converters["path"] = PathConverter
        self.url_map.converters["int"] = IntegerConverter
        self.url_map.converters["float"] = FloatConverter
        self.url_map.converters["uuid"] = UUIDConverter

        self.blueprints = {}  # type: t.Dict[str, Blueprint]
        self.cli = cli.FlaskGroup(self)
        self._before_request_funcs = {}  # type: t.Dict[t.Optional[str], t.List[T_before_request]]
        self._after_request_funcs = {}  # type: t.Dict[t.Optional[str], t.List[T_after_request]]
        self._teardown_request_funcs = {}  # type: t.Dict[t.Optional[str], t.List[T_teardown]]
        self._teardown_appcontext_funcs = []  # type: t.List[T_teardown]
        self._template_context_processors = {}  # type: t.Dict[t.Optional[str], t.List[t.Callable[[], t.Dict[str, t.Any]]]]
        self._shell_context_processors = []  # type: t.List[T_shell_context_processor]
        self.url_value_preprocessors = {}  # type: t.Dict[t.Optional[str], t.List[t.Callable[[str, t.Dict[str, t.Any]], None]]]
        self.url_default_functions = {}  # type: t.Dict[t.Optional[str], t.List[t.Callable[[str, t.Dict[str, t.Any]], None]]]

        self.extensions = {}  # type: t.Dict[str, t.Any]

        self._got_first_request = False
        self._before_first_request_functions = []  # type: t.List[t.Callable[[], None]]

        if self.has_static_folder:
            self.add_url_rule(
                self.static_url_path + "/<path:filename>",
                endpoint="static",
                host=static_host,
                view_func=self.send_static_file,
            )

        self._logger = None  # type: t.Optional[logging.Logger]
        self.name = self.import_name

        self.debug = get_debug_flag()

        self._cli_commands = {}  # type: t.Dict[str, cli.AppGroup]

    def _get_exc_class_name(self, exc_class: t.Type[Exception]) -> str:
        return exc_class.__name__

    def make_config(
        self, instance_relative: bool = False
    ) -> Config:
        """Used to create the config attribute by the Flask constructor.
        The `instance_relative` parameter is passed in from the constructor
        of Flask and indicates if the config should be relative to the
        instance path or the root path of the application.

        .. versionadded:: 0.8
        """
        root_path = self.root_path
        if instance_relative:
            root_path = self.instance_path
        return Config(root_path, self.default_config)

    def auto_find_instance_path(self) -> str:
        """Tries to locate the instance path if it was not provided when the
        application object was created.  Basically it finds the parent folder
        that contains the folder specified by the ``instance_folder``
        parameter.

        .. versionadded:: 0.8
        """
        prefix, package_path = find_package(self.import_name)

        if prefix is None:
            return os.path.abspath(os.path.join(package_path, "instance"))

        return os.path.abspath(os.path.join(prefix, "var", self.name + "-instance"))

    @property
    def logger(self) -> logging.Logger:
        """A :class:`logging.Logger` object for this application.  The
        default configuration is to log to stderr if the application is
        in debug mode.  This logger can be used to (surprise) log messages.

        Here some examples::

            app.logger.debug('A value for debugging')
            app.logger.warning('A warning occurred (%d apples)', 42)
            app.logger.error('An error occurred')

        .. versionadded:: 0.3
        """
        if self._logger is None:
            self._logger = create_logger(self)
        return self._logger

    def create_global_jinja_loader(self) -> DispatchingJinjaLoader:
        """Creates the loader for the Jinja2 environment.  Can be used to
        override just the loader and keeping the rest unchanged.
        It's discouraged to override this function.  Instead one should
        override the :meth:`jinja_loader` function instead.

        The global loader dispatches between the loaders of the application
        and the blueprints.

        .. versionadded:: 0.7
        """
        return DispatchingJinjaLoader(self)
