import os
from snovault import DBSESSION
from contextlib import contextmanager
from multiprocessing import get_context
from multiprocessing.pool import Pool
from pyramid.decorator import reify
from pyramid.request import apply_request_extensions
from pyramid.settings import asbool
from pyramid.threadlocal import (
    get_current_request,
    manager,
)
import atexit
import logging
import time
import transaction
from .indexer import (
    INDEXER,
    Indexer,
)
from .interfaces import APP_FACTORY

log = logging.getLogger(__name__)


def includeme(config):
    if config.registry.settings.get('indexer_worker'):
        return
    processes = config.registry.settings.get('indexer.processes')
    try:
        processes = int(processes)
    except:
        processes = None
    if processes and processes > 1:
        do_log = False
        if asbool(config.registry.settings.get('indexer')):
            print('Set primary MP indexer in indexer.py')
            do_log = False
        config.registry[INDEXER] = MPIndexer(config.registry, processes=processes, do_log=do_log)


# Running in subprocess

current_xmin_snapshot_id = None
app = None


def initializer(app_factory, settings):
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    global app
    atexit.register(clear_snapshot)
    app = app_factory(settings, indexer_worker=True, create_tables=False)
    signal.signal(signal.SIGALRM, clear_snapshot)


def set_snapshot(xmin, snapshot_id):
    global current_xmin_snapshot_id
    if current_xmin_snapshot_id == (xmin, snapshot_id):
        return
    clear_snapshot()
    current_xmin_snapshot_id = (xmin, snapshot_id)

    while True:
        txn = transaction.begin()
        txn.doom()
        if snapshot_id is not None:
            txn.setExtendedInfo('snapshot_id', snapshot_id)
        session = app.registry[DBSESSION]()
        connection = session.connection()
        db_xmin = connection.execute(
            "SELECT txid_snapshot_xmin(txid_current_snapshot());").scalar()
        if db_xmin >= xmin:
            break
        transaction.abort()
        log.info('Waiting for xmin %r to reach %r', db_xmin, xmin)
        time.sleep(0.1)

    registry = app.registry
    request = app.request_factory.blank('/_indexing_pool')
    request.registry = registry
    request.datastore = 'database'
    apply_request_extensions(request)
    request.invoke_subrequest = app.invoke_subrequest
    request.root = app.root_factory(request)
    request._stats = {}
    manager.push({'request': request, 'registry': registry})


def clear_snapshot(signum=None, frame=None):
    global current_xmin_snapshot_id
    if current_xmin_snapshot_id is None:
        return
    transaction.abort()
    manager.pop()
    current_xmin_snapshot_id = None


@contextmanager
def snapshot(xmin, snapshot_id):
    import signal
    signal.alarm(0)
    set_snapshot(xmin, snapshot_id)
    yield
    signal.alarm(5)


def update_object_in_snapshot(args):
    uuid, xmin, snapshot_id, restart = args
    # pid = os.getpid()
    pid = None
    with snapshot(xmin, snapshot_id):
        request = get_current_request()
        indexer = request.registry[INDEXER]
        output = indexer.update_object(
            request,
            uuid,
            xmin,
            restart=restart,
            pid=pid,
        )
        return output


# Running in main process

class MPIndexer(Indexer):
    maxtasks = 1  # pooled processes will exit and be replaced after this many tasks are completed.

    def __init__(self, registry, processes=None, do_log=False):
        super(MPIndexer, self).__init__(registry, do_log=do_log)
        self.processes = processes
        self.chunksize = int(registry.settings.get('indexer.chunk_size',1024))  # in production.ini (via buildout.cfg) as 1024
        self.initargs = (registry[APP_FACTORY], registry.settings,)

    @reify
    def pool(self):
        return Pool(
            processes=self.processes,
            initializer=initializer,
            initargs=self.initargs,
            maxtasksperchild=self.maxtasks,
            context=get_context('forkserver'),
        )

    def update_objects(self, request, uuids, xmin, snapshot_id, restart):
        # Ensure that we iterate over uuids in this thread not the pool task handler.
        uuid_count = len(uuids)
        self._indexer_log.new_log(uuid_count, xmin, snapshot_id)
        workers = 1
        if self.processes is not None and self.processes > 0:
            workers = self.processes
        chunkiness = int((uuid_count - 1) / workers) + 1
        if chunkiness > self.chunksize:
            chunkiness = self.chunksize

        tasks = [(uuid, xmin, snapshot_id, restart) for uuid in uuids]
        errors = []
        outputs = [
            {
                'chunkiness': chunkiness,
                'name': 'mpinfo',
                'processes': workers,
                'uuid_count': uuid_count,

            }
        ]
        try:
            print(outputs, os.getpid())
            for i, output in enumerate(
                    self.pool.imap_unordered(
                        update_object_in_snapshot,
                        tasks,
                        chunkiness
                    )
                ):
                outputs.append(output)
                if output['error_message']:
                    errors.append({
                        'error_message': output['error_message'],
                        'timestamp': output['end_timestamp'],
                        'uuid': output['uuid'],
                    })
                if (i + 1) % 50 == 0:
                    log.info('Indexing %d', i + 1)
        except:
            self.shutdown()
            raise
        return outputs, errors

    def shutdown(self):
        if 'pool' in self.__dict__:
            self.pool.terminate()
            self.pool.join()
            del self.pool
