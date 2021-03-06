"""hug/api.py

Defines the dynamically generated Hug API object that is responsible for storing all routes and state within a module

Copyright (C) 2016  Timothy Edmund Crosley

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED
TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

"""
from __future__ import absolute_import
import logging

import json
import sys
from collections import OrderedDict, namedtuple
from functools import partial
from itertools import chain
from types import ModuleType
import sanic
# from sanic_session import session_middleware
import uvloop

from sanic import Sanic
# from sanic_jinja2 import SanicJinja2
# from sanic_session import InMemorySessionInterface

from settings import config

from hug.middleware import not_found_middleware
import hug.defaults
import hug.output_format
from hug._version import current

INTRO = """
/##########################################################################\\
          `.----``..-------..``.----.
         :/:::::--:---------:--::::://.
        .+::::----##/-/oo+:-##----:::://
        `//::-------/oosoo-------::://.          ##    ##  ##    ##    #####
          .-:------./++o/o-.------::-`   ```     ##    ##  ##    ##  ##
             `----.-./+o+:..----.     `.:///.aio ########  ##    ## ##
   ```        `----.-::::::------  `.-:::://.    ##    ##  ##    ## ##   ####
  ://::--.``` -:``...-----...` `:--::::::-.`     ##    ##  ##   ##   ##    ##
  :/:::::::::-:-     `````      .:::::-.`        ##    ##    ####     ######
   ``.--:::::::.                .:::.`
         ``..::.      aio       .::         EMBRACE THE APIs OF THE FUTURE
             ::-                .:-
             -::`               ::-                   VERSION {0}
             `::-              -::`
              -::-`           -::-
\###########################################################################/

 Copyright (C) 2016 Timothy Edmund Crosley
 Under the MIT License

""".format(current)


class InterfaceAPI(object):
    """Defines the per-interface API which defines all shared information for a specific interface, and how it should
        be exposed
    """
    __slots__ = ('api',)

    def __init__(self, api):
        self.api = api


class HTTPInterfaceAPI(InterfaceAPI):
    """Defines the HTTP interface specific API"""
    __slots__ = ('routes', 'versions', 'base_url', '_output_format', '_input_format', 'versioned', '_middleware',
                 '_not_found_handlers', '_startup_handlers', 'sinks', '_not_found', '_exception_handlers')

    def __init__(self, api, base_url=''):
        super().__init__(api)
        self.versions = set()
        self.routes = OrderedDict()
        self.sinks = OrderedDict()
        self.versioned = OrderedDict()
        self.base_url = base_url
        self._middleware = set([not_found_middleware])

    @property
    def output_format(self):
        return getattr(self, '_output_format', hug.defaults.output_format)

    @output_format.setter
    def output_format(self, formatter):
        self._output_format = formatter

    @property
    def not_found(self):
        """Returns the active not found handler"""
        return getattr(self, '_not_found', self.base_404)

    def input_format(self, content_type):
        """Returns the set input_format handler for the given content_type"""
        return getattr(self, '_input_format', {}).get(content_type, hug.defaults.input_format.get(content_type, None))

    def set_input_format(self, content_type, handler):
        """Sets an input format handler for this Hug API, given the specified content_type"""
        if getattr(self, '_input_format', None) is None:
            self._input_format = {}
        self._input_format[content_type] = handler

    @property
    def middleware(self):
        return getattr(self, '_middleware', None)

    def add_middleware(self, middleware):
        """Adds a middleware object used to process all incoming requests against the API"""
        if self._middleware is None:
            self._middleware = set()

        self._middleware.add(middleware)

    def add_sink(self, sink, url, base_url=""):
        base_url = base_url or self.base_url
        self.sinks.setdefault(base_url, OrderedDict())
        self.sinks[base_url][url] = sink

    def exception_handlers(self, version=None):
        if not hasattr(self, '_exception_handlers'):
            return None

        return self._exception_handlers.get(version, self._exception_handlers.get(None, None))

    def add_exception_handler(self, exception_type, error_handler, versions=(None,)):
        """Adds a error handler to the hug api"""
        versions = (versions,) if not isinstance(versions, (tuple, list)) else versions
        if not hasattr(self, '_exception_handlers'):
            self._exception_handlers = {}
        for version in versions:
            self._exception_handlers.setdefault(version, OrderedDict())[exception_type] = error_handler

    def extend(self, http_api, route="", base_url=""):

        """Adds handlers from a different Hug API to this one - to create a single API"""
        self.versions.update(http_api.versions)
        base_url = base_url or self.base_url

        for router_base_url, routes in http_api.routes.items():
            self.routes.setdefault(base_url, OrderedDict())
            for item_route, handler in routes.items():
                for method, versions in handler.items():
                    for version, function in versions.items():
                        function.interface.api = self.api
                self.routes[base_url][route + item_route] = handler

        for sink_base_url, sinks in http_api.sinks.items():
            for url, sink in sinks.items():
                self.add_sink(sink, route + url, base_url=base_url)

        for middleware in (http_api.middleware or ()):
            self.add_middleware(middleware)

        for startup_handler in (http_api.startup_handlers or ()):
            self.add_startup_handler(startup_handler)

        for version, handler in getattr(self, '_exception_handlers', {}).items():
            for exception_type, exception_handler in handler.items():
                target_exception_handlers = http_api.exception_handlers(version) or {}
                if exception_type not in target_exception_handlers:
                    http_api.add_exception_handler(exception_type, exception_handler, version)

        for input_format, input_format_handler in getattr(http_api, '_input_format', {}).items():
            if not input_format in getattr(self, '_input_format', {}):
                self.set_input_format(input_format, input_format_handler)

        for version, handler in http_api.not_found_handlers.items():
            if version not in self.not_found_handlers:
                self.set_not_found_handler(handler, version)

    @property
    def not_found_handlers(self):
        return getattr(self, '_not_found_handlers', {})

    def set_not_found_handler(self, handler, version=None):
        """Sets the not_found handler for the specified version of the api"""
        if not self.not_found_handlers:
            self._not_found_handlers = {}

        self.not_found_handlers[version] = handler

    def documentation(self, base_url=None, api_version=None):
        """Generates and returns documentation for this API endpoint"""
        documentation = OrderedDict()
        base_url = self.base_url if base_url is None else base_url
        overview = self.api.module.__doc__
        if overview:
            documentation['overview'] = overview

        version_dict = OrderedDict()
        versions = self.versions
        versions_list = list(versions)
        if None in versions_list:
            versions_list.remove(None)
        if False in versions_list:
            versions_list.remove(False)
        if api_version is None and len(versions_list) > 0:
            api_version = max(versions_list)
            documentation['version'] = api_version
        elif api_version is not None:
            documentation['version'] = api_version
        if versions_list:
            documentation['versions'] = versions_list
        for router_base_url, routes in self.routes.items():
            for url, methods in routes.items():
                for method, method_versions in methods.items():
                    for version, handler in method_versions.items():
                        if getattr(handler, 'private', False):
                            continue
                        if version is None:
                            applies_to = versions
                        else:
                            applies_to = (version,)
                        for version in applies_to:
                            if api_version and version != api_version:
                                continue
                            doc = version_dict.setdefault(router_base_url + url, OrderedDict())
                            doc[method] = handler.documentation(doc.get(method, None), version=version,
                                                                base_url=router_base_url or base_url, url=url)

        documentation['handlers'] = version_dict
        return documentation

    def serve(self, port=8005, no_documentation=False):
        """Runs the basic hug development server against this API"""

        if no_documentation:
            app = self.aio_server(None)
        else:
            app = self.aio_server()

        app.run(host=config['HOST'],
                port=config['PORT'],
                debug=config['DEBUG'],
                workers=config['WORKER'])

        print(INTRO)
        print("Serving on port {0}...".format(port))

    @staticmethod
    def base_404(request, response, *kargs, **kwargs):
        """Defines the base 404 handler"""
        response.set_status(404)

    def determine_version(self, request, api_version=None):
        """Determines the appropriate version given the set api_version, the request header, and URL query params"""
        if api_version is False:
            api_version = None
            for version in self.versions:
                if version and "v{0}".format(version) in request.path:
                    api_version = version
                    break

        request_version = set()
        if api_version is not None:
            request_version.add(api_version)

        # version_header = request.get_header("X-API-VERSION")
        # if version_header:
        #     request_version.add(version_header)

        # version_param = request.get_param('api_version')
        # if version_param is not None:
        #     request_version.add(version_param)

        if len(request_version) > 1:
            raise ValueError('You are requesting conflicting versions')

        return next(iter(request_version or (None,)))

    def documentation_404(self, base_url=None):
        """Returns a smart 404 page that contains documentation for the written API"""
        base_url = self.base_url if base_url is None else base_url

        async def handle_404(request, *kargs, **kwargs):
            response = sanic.web.Response()
            url_prefix = self.base_url
            if not url_prefix:
                url_prefix = 'http://%s' % (request.host)

            to_return = OrderedDict()
            to_return['404'] = ("The API call you tried to make was not defined. "
                                "Here's a definition of the API to help you get going :)")
            to_return['documentation'] = self.documentation(url_prefix, self.determine_version(request, False))
            response.body = json.dumps(to_return, indent=4, separators=(',', ': ')).encode('utf8')
            response.set_status(404)
            response.content_type = 'application/json'
            return response

        return handle_404

    def version_router(self, request, response, api_version=None, versions={}, not_found=None, **kwargs):
        """Intelligently routes a request to the correct handler based on the version being requested"""
        request_version = self.determine_version(request, api_version)
        if request_version:
            request_version = int(request_version)
        versions.get(request_version or False, versions.get(None, not_found))(request, response,
                                                                              api_version=api_version,
                                                                              **kwargs)

    def aio_server(self, default_not_found=True):
        if config['DEBUG']:
            logging.basicConfig(level=logging.DEBUG)

        app = Sanic("hug", log_config=config['logging'])

        routesdoc = {}
        for router_base_url, routes in self.routes.items():
            for url, methods in routes.items():
                for method, versions in methods.items():
                    routers = {}
                    method_function = "on_{0}".format(method.lower())
                    routers[method_function] = {}
                    if len(versions) == 1 and None in versions.keys():
                        routers[method_function][None] = versions[None]
                    else:
                        for ver in versions.keys():
                            routers[method_function][ver] = versions[ver]

                    for method_function in routers.keys():
                        if routers[method_function].get(None):
                            app.add_route(handler=routers[method_function][None], uri=router_base_url + url,
                                          methods=[method])
                            if router_base_url + url not in routesdoc:
                                routesdoc[router_base_url + url] = {
                                    method: routers[method_function][None].interface.spec.__doc__}  # for doc
                            else:
                                routesdoc[router_base_url + url].update(
                                    {method: routers[method_function][None].interface.spec.__doc__})  # for doc

                        if self.versions and self.versions != (None,):
                            for ver in self.versions:
                                if ver == None:
                                    continue
                                if routers[method_function].get(ver):
                                    app.add_route(handler=routers[method_function][ver],
                                                  uri=router_base_url + '/v%s' % (ver) + url,
                                                  methods=[method])
                                    if router_base_url + '/v%s' % (ver) + url not in routesdoc:
                                        routesdoc[router_base_url + '/v%s' % (ver) + url] = {
                                            method: routers[method_function][ver].interface.spec.__doc__}  # for doc
                                    else:
                                        routesdoc[router_base_url + '/v%s' % (ver) + url].update(
                                            {method: routers[method_function][ver].interface.spec.__doc__})  # for doc
                                else:
                                    app.add_route(
                                        handler=routers[method_function][list(routers[method_function].keys())[0]],
                                        uri=router_base_url + '/v%s' % (ver) + url,
                                        methods=[method]
                                    )
                                    if router_base_url + '/v%s' % (ver) + url not in routesdoc:
                                        routesdoc[router_base_url + '/v%s' % (ver) + url] = {
                                            method: routers[method_function][list(routers[method_function].keys())[
                                                0]].interface.spec.__doc__}  # for doc
                                    else:
                                        routesdoc[router_base_url + '/v%s' % (ver) + url].update({method: routers[
                                            method_function][list(routers[method_function].keys())[
                                            0]].interface.spec.__doc__})  # for doc

        # if DEBUG:
        #     setup_swagger(app, routesdoc)

        default_not_found = self.documentation_404() if default_not_found is True else None
        not_found_handler = default_not_found
        if self.not_found_handlers:
            if len(self.not_found_handlers) == 1 and None in self.not_found_handlers:
                not_found_handler = self.not_found_handlers[None]

        if not_found_handler:
            app._not_found = not_found_handler
        return app

    async def shutdown(self, app):
        await app.shutdown()
        await app.cleanup()

    def server(self, default_not_found=True, base_url=None):
        """Returns a WSGI compatible API server for the given Hug API module"""
        loop = asyncio.get_event_loop()
        if default_not_found:
            app = self.aio_server(loop, None)
        else:
            app = self.aio_server(loop)
        return app

    @property
    def startup_handlers(self):
        return getattr(self, '_startup_handlers', ())

    def add_startup_handler(self, handler):
        """Adds a startup handler to the hug api"""
        if not self.startup_handlers:
            self._startup_handlers = []

        self.startup_handlers.append(handler)


HTTPInterfaceAPI.base_404.interface = True


class CLIInterfaceAPI(InterfaceAPI):
    """Defines the CLI interface specific API"""
    __slots__ = ('commands',)

    def __init__(self, api, version=''):
        super().__init__(api)
        self.commands = {}

    def __call__(self):
        """Routes to the correct command line tool"""
        if not len(sys.argv) > 1 or not sys.argv[1] in self.commands:
            return sys.exit(1)

        command = sys.argv.pop(1)
        self.commands.get(command)()

    def __str__(self):
        return "{0}\n\nAvailable Commands:{1}\n".format(self.api.module.__doc__ or self.api.module.__name__,
                                                        "\n\n\t- " + "\n\t- ".join(self.commands.keys()))


class ModuleSingleton(type):
    """Defines the module level __hug__ singleton"""

    def __call__(cls, module, *args, **kwargs):
        if isinstance(module, API):
            return module

        if type(module) == str:
            if module not in sys.modules:
                sys.modules[module] = ModuleType(module)
            module = sys.modules[module]

        if not '__hug__' in module.__dict__:
            def api_auto_instantiate(*kargs, **kwargs):
                if not hasattr(module, '__hug_serving__'):
                    module.__hug_wsgi__ = module.__hug__.http.server()
                    module.__hug_serving__ = True
                return module.__hug_wsgi__(*kargs, **kwargs)

            module.__hug__ = super().__call__(module, *args, **kwargs)
            module.__hug_wsgi__ = api_auto_instantiate
        return module.__hug__


class API(object, metaclass=ModuleSingleton):
    """Stores the information necessary to expose API calls within this module externally"""
    __slots__ = ('module', '_directives', '_http', '_cli', '_context')

    def __init__(self, module):
        self.module = module

    def directives(self):
        """Returns all directives applicable to this Hug API"""
        directive_sources = chain(hug.defaults.directives.items(), getattr(self, '_directives', {}).items())
        return {'hug_' + directive_name: directive for directive_name, directive in directive_sources}

    def directive(self, name, default=None):
        """Returns the loaded directive with the specified name, or default if passed name is not present"""
        return getattr(self, '_directives', {}).get(name, hug.defaults.directives.get(name, default))

    def add_directive(self, directive):
        self._directives = getattr(self, '_directives', {})
        self._directives[directive.__name__] = directive

    @property
    def http(self):
        if not hasattr(self, '_http'):
            self._http = HTTPInterfaceAPI(self)
        return self._http

    @property
    def cli(self):
        if not hasattr(self, '_cli'):
            self._cli = CLIInterfaceAPI(self)
        return self._cli

    @property
    def context(self):
        if not hasattr(self, '_context'):
            self._context = {}
        return self._context

    def extend(self, api, route="", base_url=""):
        """Adds handlers from a different Hug API to this one - to create a single API"""
        api = API(api)

        if hasattr(api, '_http'):
            self.http.extend(api.http, route, base_url)

        for directive in getattr(api, '_directives', {}).values():
            self.add_directive(directive)


def from_object(obj):
    """Returns a Hug API instance from a given object (function, class, instance)"""
    return API(obj.__module__)
