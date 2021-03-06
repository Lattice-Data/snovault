import pytest
from selenium.webdriver.chrome.options import Options

pytest_plugins = [
    'snowflakes.tests.features.browsersteps',
    'snowflakes.tests.features.customsteps',
]


def pytest_configure():
    import logging
    logging.basicConfig()
    logging.getLogger('selenium').setLevel(logging.DEBUG)


@pytest.fixture
def external_tx():
    pass


@pytest.fixture(scope='session')
def app_settings(wsgi_server_host_port, elasticsearch_server, postgresql_server):
    from snovault.tests.testappfixtures import _app_settings
    settings = _app_settings.copy()
    settings['create_tables'] = True
    settings['elasticsearch.server'] = elasticsearch_server
    settings['sqlalchemy.url'] = postgresql_server
    settings['collection_datastore'] = 'elasticsearch'
    settings['item_datastore'] = 'elasticsearch'
    settings['snovault.elasticsearch.index'] = 'snovault'
    settings['indexer'] = True
    settings['indexer.processes'] = 2
    settings['queue_type'] = 'Simple'
    settings['queue_server'] = True
    settings['queue_worker'] = True
    settings['queue_worker_processes'] = 2
    settings['queue_worker_chunk_size'] = 1024
    settings['queue_worker_batch_size'] = 2000
    return settings


@pytest.yield_fixture(scope='session')
def app(app_settings):
    from snowflakes import main
    from snovault.elasticsearch import create_mapping
    app = main({}, **app_settings)

    create_mapping.run(app)
    yield app

    # Shutdown multiprocessing pool to close db conns.
    from snovault.elasticsearch import INDEXER
    app.registry[INDEXER].shutdown()

    from snovault import DBSESSION
    DBSession = app.registry[DBSESSION]
    # Dispose connections so postgres can tear down.
    DBSession.bind.pool.dispose()


@pytest.mark.fixture_cost(500)
@pytest.yield_fixture(scope='session')
def workbook(app):
    from webtest import TestApp
    environ = {
        'HTTP_ACCEPT': 'application/json',
        'REMOTE_USER': 'TEST',
    }
    testapp = TestApp(app, environ)

    from ...loadxl import load_all
    from pkg_resources import resource_filename
    inserts = resource_filename('snowflakes', 'tests/data/inserts/')
    docsdir = [resource_filename('snowflakes', 'tests/data/documents/')]
    load_all(testapp, inserts, docsdir)

    testapp.post_json('/index', {})
    yield
    # XXX cleanup


@pytest.fixture(scope='session')
def wsgi_server_app(app):
    from http.cookies import SimpleCookie

    def wsgi_filter(environ, start_response):
        # set REMOTE_USER from cookie
        cookies = SimpleCookie()
        cookies.load(environ.get('HTTP_COOKIE', ''))
        if 'REMOTE_USER' in cookies:
            user = cookies['REMOTE_USER'].value
        else:
            user = 'TEST_AUTHENTICATED'
        environ['REMOTE_USER'] = user
        return app(environ, start_response)
    return wsgi_filter


@pytest.fixture(scope='session')
def base_url(wsgi_server):
    return wsgi_server


@pytest.fixture(scope='session')
def splinter_driver_kwargs(request):
    kwargs = dict(request.config.option.browser_args or ())
    arguments = get_chrome_webdriver_options(request)
    if arguments:
        kwargs['options'] = set_chrome_webdriver_options(arguments)
    return kwargs


@pytest.fixture(scope='session')
def splinter_window_size():
    # Sauce Labs seems to only support 1024x768.
    return (1024, 768)


# Depend on workbook fixture here to avoid remote browser timeouts.
@pytest.fixture(scope='session')
def browser(workbook, session_browser):
    return session_browser


@pytest.yield_fixture(scope='session')
def admin_user(browser, base_url):
    browser.visit(base_url)  # need to be on domain to set cookie
    browser.cookies.add({'REMOTE_USER': 'TEST'})
    yield
    browser.cookies.delete('REMOTE_USER')


@pytest.yield_fixture(scope='session')
def submitter_user(browser, base_url, admin_user):
    browser.visit(base_url + '/#!impersonate-user')
    browser.find_by_name('userid').first.fill('massa.porta@varius.mauris')
    browser.find_by_text('Submit').first.click()
    browser.is_text_present('J. Michael Cherry', wait_time=15)
    yield
    browser.visit(base_url + '/logout')


@pytest.fixture
def pytestbdd_strict_gherkin():
    return False


# https://github.com/pytest-dev/pytest-bdd/issues/117


def write_line(request, when, line):
    """Write line instantly."""
    terminal = request.config.pluginmanager.getplugin('terminalreporter')
    if terminal.verbosity <= 0:
        return
    capman = request.config.pluginmanager.getplugin('capturemanager')
    outerr = capman.suspend_global_capture()
    try:
        if outerr is not None:
            out, err = outerr
            request.node.add_report_section(when, 'out', out)
            request.node.add_report_section(when, 'err', err)
        terminal.write_line(line)
    finally:
        capman.resume_global_capture()


@pytest.mark.trylast
def pytest_bdd_before_scenario(request, feature, scenario):
    write_line(request, 'setup', u'Scenario: {scenario.name}'.format(scenario=scenario))


@pytest.mark.trylast
def pytest_bdd_step_error(request, feature, scenario, step, step_func, step_func_args, exception):
    write_line(request, 'call', u'Step: {step.name} FAILED'.format(step=step))


@pytest.mark.trylast
def pytest_bdd_after_step(request, feature, scenario, step, step_func, step_func_args):
    write_line(request, 'call', u'Step: {step.name} PASSED'.format(step=step))


def get_chrome_webdriver_options(request):
    arguments = request.config.option.chrome_options or ''
    arguments = arguments.split()
    return arguments


def set_chrome_webdriver_options(arguments):
    options = Options()
    for argument in arguments:
        options.add_argument(argument)
    return options
