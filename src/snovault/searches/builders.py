from .fields import AbstractResponseField


class ViewBuilder():

    def __init__(self, response_fields=[]):
        self.response = {}
        self.response_fields = response_fields
        self.validate_response_fields()

    def validate_response_fields(self):
        for f in self.response_fields:
            if not isinstance(f, AbstractResponseField):
                raise ValueError(
                    '{} must be of type AbstractResponseField'.format(
                        f.__class__.__name__
                    )
                )

    def render(self):
        '''
        Expects response_fields will return dictionaries with unique keys.
        '''
        for f in self.response_fields:
            self.response.update(f.render())
        return self.response


class QueryBuilder():

    def __init__(self, query=None, defaults=None):
        self.query = query
        self.defaults = defaults

    def add_query_string(self):
        pass

    def add_filter(self):
        pass

    def add_post_filter(self):
        pass

    def add_sort_by(self):
        pass

    def add_indices(self):
        pass

    def add_aggs(self):
        pass

    def build_query(self):
        pass


class QueryResultBuilder():

    def __init__(self, query, query_submitter):
        self.query = query
        self.query_submitter = query_submitter

    def get_results():
        pass


class ResponseBuilder():

    def __init__(self):
        self._response = {}

    def get_response_template(self):
        pass

    def add_frontend_fields(self):
        pass

    def add_search_results(self):
        pass
