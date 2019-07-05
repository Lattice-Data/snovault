import pytest


def test_searches_responses_fielded_response_init():
    from snovault.elasticsearch.searches.responses import FieldedResponse
    fr = FieldedResponse()
    assert isinstance(fr, FieldedResponse)


def test_searches_responses_fielded_response_validate_response_fields():
    from snovault.elasticsearch.searches.responses import FieldedResponse
    from snovault.elasticsearch.searches.fields import ResponseField
    rf = ResponseField()
    FieldedResponse(response_fields=[rf])
    class NewResponseField(ResponseField):
        def __init__(self):
            super().__init__()
    nrf = NewResponseField()
    FieldedResponse(response_fields=[rf, nrf])
    class OtherResponseField():
        pass
    orf = OtherResponseField()
    with pytest.raises(ValueError):
        FieldedResponse(response_fields=[rf, nrf, orf])


def test_searches_responses_query_response_init():
    assert False


def test_searches_responses_basic_query_response_with_facets():
    assert False
