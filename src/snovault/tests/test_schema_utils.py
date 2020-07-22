from snovault.schema_utils import validate
import pytest


targets = [
    {'name': 'one', 'uuid': '775795d3-4410-4114-836b-8eeecf1d0c2f'},
]


@pytest.fixture
def content(testapp):
    url = '/testing-link-targets/'
    for item in targets:
        testapp.post_json(url, item, status=201)


def test_uniqueItems_validates_normalized_links(content, threadlocals):
    schema = {
        'uniqueItems': True,
        'items': {
            'linkTo': 'TestingLinkTarget',
        }
    }
    uuid = targets[0]['uuid']
    data = [
        uuid,
        '/testing-link-targets/{}'.format(uuid),
    ]
    validated, errors = validate(schema, data)
    assert len(errors) == 1
    assert (
        errors[0].message == "['{}', '{}'] has non-unique elements".format(
            uuid, uuid)
    )


def test_pattern_validates_strings_or_array_of_strings(content, threadlocals):
    schema = {
        'items': {
            'pattern': 'option1|option2'
        }
    }

    uuid = 'option2'
    data = [
        uuid,
        '/testing-link-targets/{}'.format(uuid),
    ]

    validated, errors = validate(schema, data)
    assert len(errors) == 0

    schema['items']['pattern'] = ['option1|', 'option2']
    validated, errors = validate(schema, data)
    assert len(errors) == 0

    uuid = 'option3'
    data = [
        uuid,
        '/testing-link-targets/{}'.format(uuid),
    ]

    validated, errors = validate(schema, data)
    assert len(errors) > 0
    assert errors[0].message == "'option3' does not match 'option1|option2'"

    schema['items']['pattern'] = ['option1|', 'option2']
    validated, errors = validate(schema, data)
    assert len(errors) > 0
    assert errors[0].message == "'option3' does not match 'option1|option2'"
