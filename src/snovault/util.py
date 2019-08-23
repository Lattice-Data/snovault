import time
from past.builtins import basestring
from pyramid.threadlocal import manager as threadlocal_manager
from snovault.interfaces import ROOT


def includeme(config):
    config.add_request_method(select_distinct_values)


def get_root_request():
    if threadlocal_manager.stack:
        return threadlocal_manager.stack[0]['request']


def ensurelist(value):
    if isinstance(value, basestring):
        return [value]
    return value


def simple_path_ids(obj, path):
    if isinstance(path, basestring):
        path = path.split('.')
    if not path:
        yield obj
        return
    name = path[0]
    remaining = path[1:]
    value = obj.get(name, None)
    if value is None:
        return
    if not isinstance(value, list):
        value = [value]
    for member in value:
        for result in simple_path_ids(member, remaining):
            yield result


def expand_path(request, obj, path):
    print('')
    print('util.py:expand_path', 'start', path)
    if isinstance(path, basestring):
        path = path.split('.')
    if not path:
        return
    name = path[0]
    remaining = path[1:]
    value = obj.get(name, None)
    if value is None:
        print('util.py:expand_path', 'value is None', path)
        return
    if isinstance(value, list):
        print('util.py:expand_path', 'isinstance(value, list)', 'loop start',
                path)
        for index, member in enumerate(value):
            if not isinstance(member, dict):
                print('util.py:expand_path', 'isinstance(value, list)', 'not dict', path)
                start_time_sub = time.time()
                member = value[index] = request.embed(member, '@@object')
                print('util.py:expand_path', 'isinstance(value, list)',
                        '@@object', '%s %.6f' % (member, time.time() -
                            start_time_sub), path)
            print('util.py:expand_path', 'isinstance(value, list)', 'last')
            start_time = time.time()
            expand_path(request, member, remaining)
            print('util.py:expand_path', 'isinstance(value, list)', 'expand_path', '%s %.6f' % (remaining, time.time() - start_time))
        print('util.py:expand_path', 'isinstance(value, list)', 'loop end')
    else:
        print('util.py:expand_path', 'ELSE')
        if not isinstance(value, dict):
            print('util.py:expand_path', 'ELSE', 'not isinstance(value, dict)')
            start_time_sub = time.time()
            # @@object endpoint resource_views.py:item_view_object
            print('util.py:expand_path', 'ELSE', 'embed @@object', value)
            value = obj[name] = request.embed(value, '@@object')
            print('util.py:expand_path', 'ELSE', '@@object', '%s %.6f' % (value, time.time() - start_time_sub))
        print('util.py:expand_path', 'ELSE', 'last')
        start_time = time.time()
        expand_path(request, value, remaining)
        print('util.py:expand_path', 'ELSE', 'expand_path', '%s %.6f' % (remaining, time.time() - start_time))


def _get_calculated_properties_from_paths(request, paths):
    root = request.registry[ROOT]
    calculated_fields = set()
    # Expect paths in form ['/item1/identifier/', '/item2/identifier/', ...].
    item_types = {
        root.collections.get(p.split('/')[1]).type_info.item_type
        for p in paths
        if len(p.split('/')) == 4 and p.split('/')[1] in root.collections
    }
    for item_type in item_types:
        item_cls = request.registry['types'].by_item_type.get(item_type).factory
        calculated_fields.update(
            root.collections.get(item_type).type_info.calculated_properties.props_for(item_cls).keys()
        )
    return list(calculated_fields)


def select_distinct_values(request, value_path, *from_paths):
    if isinstance(value_path, basestring):
        value_path = value_path.split('.')
    values = from_paths
    for name in value_path:
        calculated_properties = _get_calculated_properties_from_paths(request, values)
        # Don't waste time calculating properties if the field isn't calculated.
        frame = '@@object' if name in calculated_properties else '@@object?skip_calculated=true'
        objs = (request.embed(member, frame) for member in values)
        value_lists = (ensurelist(obj.get(name, [])) for obj in objs)
        values = {value for value_list in value_lists for value in value_list}
    return list(values)


def quick_deepcopy(obj):
    """Deep copy an object consisting of dicts, lists, and primitives.

    This is faster than Python's `copy.deepcopy` because it doesn't
    do bookkeeping to avoid duplicating objects in a cyclic graph.

    This is intended to work fine for data deserialized from JSON,
    but won't work for everything.
    """
    if isinstance(obj, dict):
        obj = {k: quick_deepcopy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        obj = [quick_deepcopy(v) for v in obj]
    return obj


def mutated_schema(schema, mutator):
    """Apply a change to all levels of a schema.

    Returns a new schema rather than modifying the original.
    """
    schema = mutator(schema.copy())
    if 'items' in schema:
        schema['items'] = mutated_schema(schema['items'], mutator)
    if 'properties' in schema:
        schema['properties'] = schema['properties'].copy()
        for k, v in schema['properties'].items():
            schema['properties'][k] = mutated_schema(v, mutator)
    return schema
