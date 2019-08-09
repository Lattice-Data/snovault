from collections import OrderedDict
from elasticsearch_dsl import A
from elasticsearch_dsl import Q
from elasticsearch_dsl import Search
from antlr4 import IllegalStateException
from lucenequery import dialects
from lucenequery.prefixfields import prefixfields
from pyramid.httpexceptions import HTTPBadRequest
from snovault.elasticsearch import ELASTIC_SEARCH
from snovault.elasticsearch.interfaces import RESOURCES_INDEX
from snovault.interfaces import TYPES

from .decorators import assert_none_returned
from .decorators import assert_one_returned
from .decorators import assert_one_or_none_returned
from .decorators import deduplicate
from .defaults import BASE_AUDIT_FACETS
from .defaults import BASE_FIELD_FACETS
from .defaults import BASE_RETURN_FIELDS
from .defaults import BASE_SEARCH_FIELDS
from .defaults import DEFAULT_FRAMES
from .defaults import INTERNAL_AUDIT_FACETS
from .defaults import MAX_ES_RESULTS_WINDOW
from .defaults import NOT_FILTERS
from .interfaces import ALL
from .interfaces import AND
from .interfaces import AND_JOIN
from .interfaces import AND_NOT_JOIN
from .interfaces import AUDIT
from .interfaces import BOOL
from .interfaces import BOOST_VALUES
from .interfaces import COLUMNS
from .interfaces import DASH
from .interfaces import EMBEDDED
from .interfaces import EMBEDDED_TYPE
from .interfaces import EXCLUDE
from .interfaces import EXISTS
from .interfaces import FACETS
from .interfaces import FILTERS
from .interfaces import GROUP_SUBMITTER
from .interfaces import ITEM
from .interfaces import LIMIT_KEY
from .interfaces import LENGTH
from .interfaces import LONG
from .interfaces import NOT_JOIN
from .interfaces import NO
from .interfaces import PERIOD
from .interfaces import PICKER
from .interfaces import PRINCIPALS_ALLOWED_VIEW
from .interfaces import QUERY_STRING
from .interfaces import SEARCH_AUDIT
from .interfaces import _SOURCE
from .interfaces import TITLE
from .interfaces import TERMS
from .interfaces import TYPE_KEY
from .interfaces import WILDCARD
from .interfaces import YES


class AbstractQueryFactory:
    '''
    Interface for building specific queries. Don't change functionality here, instead
    inherit and extend/override functions as needed.
    '''

    def __init__(self, params_parser, *args, **kwargs):
        self.search = None
        self.params_parser = params_parser
        self.args = args
        self.kwargs = kwargs

    def _get_or_create_search(self):
        if self.search is None:
            self.search = Search(
                using=self._get_client(),
                index=self._get_index(),
            )
        return self.search

    def _get_client(self):
        return self.kwargs.get('client') or self.params_parser._request.registry[ELASTIC_SEARCH]

    def _get_index(self):
        return RESOURCES_INDEX

    def _get_principals(self):
        return self.params_parser._request.effective_principals

    def _get_registered_types(self):
        return self.params_parser._request.registry[TYPES]

    def _get_schema_for_item_type(self, item_type):
        return self._get_registered_types()[item_type].schema

    def _get_subtypes_for_item_type(self, item_type):
        return self._get_registered_types()[item_type].subtypes

    def _get_boost_values_for_item_type(self, item_type):
        return self._get_schema_for_item_type(item_type).get(BOOST_VALUES, {})

    def _get_facets_for_item_type(self, item_type):
        return self._get_schema_for_item_type(item_type).get(FACETS, {}).items()

    def _get_columns_for_item_type(self, item_type):
        return self._get_schema_for_item_type(item_type).get(COLUMNS, {})

    def _get_columns_for_item_types(self, item_types=None):
        columns = OrderedDict()
        item_type_values = item_types or self.params_parser.param_values_to_list(
            params=self._get_item_types() or self._get_default_item_types()
        )
        for item_type in item_type_values:
            columns.update(self._get_columns_for_item_type(item_type))
        return columns

    def _get_invalid_item_types(self, item_types):
        registered_types = self._get_registered_types()
        return [
            item_type
            for item_type in item_types
            if item_type not in registered_types
        ]

    def _normalize_item_types(self, item_types):
        registered_types = self._get_registered_types()
        return [
            registered_types[item_type].name
            for item_type in item_types
        ]

    def _validated_query_string_query(self, query):
        try:
            query = prefixfields(EMBEDDED, query, dialects.elasticsearch)
        except IllegalStateException:
            msg = "Invalid query: {}".format(query)
            raise HTTPBadRequest(explanation=msg)
        return query.getText()

    def _get_default_item_types(self):
        mode = self.params_parser.get_one_value(
            params=self._get_mode()
        )
        if mode == PICKER:
            item_types = [ITEM]
        else:
            item_types = self.kwargs.get('default_item_types', [])
        return [
            (TYPE_KEY, item_type)
            for item_type in item_types
        ]

    def _wildcard_in_item_types(self, item_types):
        return self.params_parser.is_param(
            TYPE_KEY,
            WILDCARD,
            params=item_types
        )

    def _get_item_types(self):
        item_types = self.params_parser.get_type_filters()
        if self._wildcard_in_item_types(item_types):
            return [(TYPE_KEY, ITEM)]
        return item_types

    def _show_internal_audits(self):
        conditions = [
            self.params_parser._request.has_permission(SEARCH_AUDIT),
            GROUP_SUBMITTER in self._get_principals()
        ]
        return all(conditions)

    def _get_audit_facets(self):
        if self._show_internal_audits():
            return BASE_AUDIT_FACETS.copy() + INTERNAL_AUDIT_FACETS.copy()
        return BASE_AUDIT_FACETS.copy()

    def _get_default_facets(self):
        return self.kwargs.get(
            'default_facets',
            BASE_FIELD_FACETS.copy()
        )

    def _get_default_and_maybe_item_facets(self):
        facets = self._get_default_facets()
        item_type_values = self.params_parser.param_values_to_list(
            params=self._get_item_types()
        )
        if len(item_type_values) == 1:
            facets.extend(
                self._get_facets_for_item_type(
                    item_type_values[0]
                )
            )
        # Add these at end.
        facets.extend(self._get_audit_facets())
        return facets

    def _get_query(self):
        must = (
            self.params_parser.get_must_match_search_term_filters()
            + self.params_parser.get_must_match_advanced_query_filters()
        )
        must_not = (
            self.params_parser.get_must_not_match_search_term_filters()
            + self.params_parser.get_must_not_match_advanced_query_filters()
        )
        return self._combine_search_term_queries(
            must_match_filters=must,
            must_not_match_filters=must_not
        )

    def _get_filters(self):
        return self.params_parser.get_not_keys_filters(not_keys=NOT_FILTERS)

    def _get_post_filters(self):
        return self.kwargs.get(
            'post_filters',
            self._get_filters() + self._get_item_types()
        )

    def _get_sort(self):
        return self.params_parser.get_sort()

    def _get_default_limit(self):
        return [(LIMIT_KEY, 25)]

    @assert_one_or_none_returned(error_message='Invalid to specify multiple limit parameters:')
    def _get_limit(self):
        return self.params_parser.get_limit() or self._get_default_limit()

    def _get_limit_value(self):
        return self.params_parser.maybe_int(
            self.params_parser.get_one_value(
                params=self._get_limit()
            )
        )

    def _get_int_limit_value(self):
        return self.params_parser.coerce_value_to_int_or_return_none(
            self._get_limit_value()
        ) or self.params_parser.get_one_value(
            params=self._get_default_limit()
        )

    def _limit_is_all(self):
        return self._get_limit_value() == ALL

    def _limit_is_over_maximum_window(self):
        limit = self._get_int_limit_value()
        if limit:
            return limit > MAX_ES_RESULTS_WINDOW
        return False

    def _should_scan_over_results(self):
        conditions = [
            self._limit_is_all(),
            self._limit_is_over_maximum_window()
        ]
        return any(conditions)

    def _get_bounded_int_limit_value_or_default(self):
        default_limit = self.params_parser.get_one_value(
            params=self._get_default_limit()
        )
        if self._should_scan_over_results():
            return default_limit
        return self._get_int_limit_value()

    @assert_one_or_none_returned(error_message='Invalid to specify multiple mode parameters:')
    def _get_mode(self):
        return self.params_parser.get_mode()

    @assert_one_or_none_returned(error_message='Invalid to specify multiple frame parameters:')
    def _get_frame(self):
        return self.params_parser.get_frame()

    def _get_frame_value(self):
        return self.params_parser.get_one_value(
            params=self._get_frame()
        )

    def _get_fields(self):
        return self.params_parser.get_field_filters()

    def _get_search_fields(self):
        return BASE_SEARCH_FIELDS.copy()

    def _get_return_fields_from_field_params(self, fields):
        return self._prefix_values(
            EMBEDDED,
            self.params_parser.param_values_to_list(
                params=fields
            )
        )

    def _get_return_fields_from_schema_columns(self):
        columns = self._get_columns_for_item_types()
        return self._prefix_values(
            EMBEDDED,
            [c for c in columns]
        )

    @deduplicate
    def _get_return_fields(self):
        # Copy to avoid modifying template.
        return_fields = BASE_RETURN_FIELDS.copy()
        fields = self._get_fields()
        frame = self._get_frame_value()
        if fields:
            return return_fields + self._get_return_fields_from_field_params(fields)
        elif frame in DEFAULT_FRAMES:
            return_fields = [frame + PERIOD + WILDCARD]
        else:
            return_fields.extend(self._get_return_fields_from_schema_columns())
        return return_fields + [AUDIT + PERIOD + WILDCARD]

    def _get_facets(self):
        return self.kwargs.get('facets', self._get_default_and_maybe_item_facets())

    def _get_facet_size(self):
        return self.kwargs.get('facet_size')

    def _prefix_value(self, prefix, value):
        return prefix + value

    def _prefix_values(self, prefix, values):
        return [
            self._prefix_value(prefix, v)
            for v in values
        ]

    def _combine_search_term_queries(self, must_match_filters=[], must_not_match_filters=[]):
        must = AND_JOIN.join(['({})'.format(q[1]) for q in must_match_filters])
        must_not = AND_NOT_JOIN.join(['({})'.format(q[1]) for q in must_not_match_filters])
        if must and must_not:
            return must + AND_NOT_JOIN + must_not
        elif must:
            return must
        elif must_not:
            return NOT_JOIN.lstrip() + must_not

    def _make_query_string_query(self, query, fields, default_operator=AND):
        return Q(
            QUERY_STRING,
            query=query,
            fields=fields,
            default_operator=default_operator
        )

    def _make_bool_query(self, **kwargs):
        return Q(
            BOOL,
            **kwargs
        )

    def _make_queries_from_params(self, query_context, params):
        return [
            query_context(
                field=self._map_param_key_to_elasticsearch_field(param_key=field),
                terms=terms
            )
            for field, terms in self.params_parser.group_values_by_key(
                    self.params_parser.remove_not_flag(
                        params=params
                    )
            ).items()
        ]

    def _make_must_equal_terms_query(self, field, terms, **kwargs):
        return Q(
            TERMS,
            **{field: terms}
        )

    def _make_must_equal_terms_queries_from_params(self, params):
        return self._make_queries_from_params(
            query_context=self._make_must_equal_terms_query,
            params=params
        )

    def _make_field_must_exist_query(self, field, **kwargs):
        return Q(
            EXISTS,
            field=field
        )

    def _make_field_must_exist_query_from_params(self, params):
        return self._make_queries_from_params(
            query_context=self._make_field_must_exist_query,
            params=params
        )

    def _make_default_filters(self):
        return [
            self._make_must_equal_terms_query(
                field=PRINCIPALS_ALLOWED_VIEW,
                terms=self._get_principals()
            ),
            self._make_must_equal_terms_query(
                field=EMBEDDED_TYPE,
                terms=(
                    self.params_parser.param_values_to_list(
                        params=self.params_parser.get_must_match_filters(
                            params=self._get_item_types()
                        )
                    )
                    or self.params_parser.param_values_to_list(
                        params=self._get_default_item_types()
                    )
                )
            )
        ]

    def _make_split_filter_queries(self, params=None):
        '''
        Returns appropriate queries from param filters.
        '''
        _must, _must_not, _exists, _not_exists = self.params_parser.split_filters_by_must_and_exists(
            params=params or self._get_post_filters()
        )
        must = self._make_must_equal_terms_queries_from_params(_must)
        must_not = self._make_must_equal_terms_queries_from_params(_must_not)
        exists = self._make_field_must_exist_query_from_params(_exists)
        not_exists = self._make_field_must_exist_query_from_params(_not_exists)
        return must, must_not, exists, not_exists

    def _make_terms_aggregation(self, field, exclude=[], size=200, *kwargs):
        return A(
            TERMS,
            field=field,
            size=size,
            exclude=exclude
        )

    def _make_exists_aggregation(self, field, **kwargs):
        return A(
            FILTERS,
            filters={
                YES: Q(EXISTS, field=field),
                NO: ~Q(EXISTS, field=field)
            }
        )

    def _make_filter_aggregation(self, filter_context, **kwargs):
        return A(
            'filter',
            filter_context
        )

    def _make_filter_and_subaggregation(self, title, filter_context, subaggregation):
        a = self._make_filter_aggregation(filter_context)
        a.bucket(title, subaggregation)
        return a

    def _map_param_key_to_elasticsearch_field(self, param_key):
        '''
        Special rules for mapping param key to actual field in ES.
        For exampe type -> embedded.@type.
        '''

        if param_key == TYPE_KEY:
            return EMBEDDED_TYPE
        elif param_key.startswith(AUDIT):
            return param_key
        else:
            return self._prefix_value(EMBEDDED, param_key)

    def _map_params_to_elasticsearch_fields(self, params):
        '''
        Like _map_param_key_to_elasticsearch_field but used for iterating over list
        of param tuples.
        '''
        for param_key, param_value in params:
            yield (
                self._map_param_key_to_elasticsearch_field(param_key),
                param_value
            )

    def _subaggregation_factory(self, facet_option_type):
        if facet_option_type == EXISTS:
            return self._make_exists_aggregation
        return self._make_terms_aggregation

    def _add_must_equal_terms_filter(self, field, terms):
        self.search = self._get_or_create_search().filter(
            self._make_must_equal_terms_query(
                field=field,
                terms=terms
            )
        )

    def _add_must_equal_terms_post_filter(self, field, terms):
        self.search = self._get_or_create_search().post_filter(
            self._make_must_equal_terms_query(
                field=field,
                terms=terms
            )
        )

    def _add_must_not_equal_terms_filter(self, field, terms):
        self.search = self._get_or_create_search().exclude(
            self._make_must_equal_terms_query(
                field=field,
                terms=terms
            )
        )

    def _add_must_not_equal_terms_post_filter(self, field, terms):
        self.search = self._get_or_create_search().post_filter(
            self._make_bool_query(
                filter=[
                    ~self._make_must_equal_terms_query(
                        field=field,
                        terms=terms
                    )
                ]
            )
        )

    def _add_field_must_exist_filter(self, field):
        self.search = self._get_or_create_search().query(
            self._make_bool_query(
                filter=[
                    self._make_field_must_exist_query(field=field),
                ]
            )
        )

    def _add_field_must_exist_post_filter(self, field):
        self.search = self._get_or_create_search().post_filter(
            self._make_bool_query(
                filter=[
                    self._make_field_must_exist_query(field=field),
                ]
            )
        )

    def _add_field_must_not_exist_filter(self, field):
        self.search = self._get_or_create_search().query(
            self._make_bool_query(
                filter=[
                    ~self._make_field_must_exist_query(field=field),
                ]
            )
        )

    def _add_field_must_not_exist_post_filter(self, field):
        self.search = self._get_or_create_search().post_filter(
            self._make_bool_query(
                filter=[
                    ~self._make_field_must_exist_query(field=field),
                ]
            )
        )

    def _add_terms_aggregation(self, title, field, exclude=[], size=200):
        self._get_or_create_search().aggs.bucket(
            title,
            self._make_terms_aggregation(
                field=field,
                size=size,
                exclude=exclude
            )
        )

    def _add_exists_aggregation(self, title, field):
        self._get_or_create_search().aggs.bucket(
            title,
            self._make_exists_aggregation(
                field=field
            )
        )

    @assert_none_returned(error_message='Invalid types:')
    def validate_item_types(self, item_types=[]):
        return self._get_invalid_item_types(
            item_types or self.params_parser.param_values_to_list(
                params=self._get_item_types()
            )
        )

    def add_query_string_query(self):
        query = self._get_query()
        if query:
            query = self._validated_query_string_query(query)
            self.search = self._get_or_create_search().query(
                self._make_query_string_query(
                    query=query,
                    fields=self._get_search_fields(),
                    default_operator=AND
                )
            )

    def add_filters(self):
        '''
        These filters apply to the entire aggregation and result context.
        '''
        self.search = self._get_or_create_search().query(
            self._make_bool_query(
                must=self._make_default_filters()
            )
        )

    def add_aggregations_and_aggregation_filters(self):
        '''
        Each aggregation is computed in a filter context that filters
        everything but the params of the same type.
        '''
        params = self._get_post_filters()
        for facet_name, facet_options in self._get_facets():
            filtered_params = self.params_parser.get_not_keys_filters(
                not_keys=[facet_name],
                params=params
            )
            must, must_not, exists, not_exists = self._make_split_filter_queries(
                params=filtered_params
            )
            subaggregation = self._subaggregation_factory(
                facet_options.get(TYPE_KEY)
            )
            subaggregation = subaggregation(
                field=self._map_param_key_to_elasticsearch_field(facet_name),
                exclude=facet_options.get(EXCLUDE, []),
                # TODO: size should be defined in schema instead of long keyword.
                size=3000 if facet_options.get(LENGTH) == LONG else 200
            )
            agg = self._make_filter_and_subaggregation(
                title=facet_name.replace(PERIOD, DASH),
                filter_context=self._make_bool_query(
                    must=must + exists,
                    must_not=must_not + not_exists
                ),
                subaggregation=subaggregation
            )
            self._get_or_create_search().aggs.bucket(facet_options.get(TITLE), agg)

    def add_post_filters(self):
        '''
        These filters apply to the final results returned, after aggregation
        has been computed.
        '''
        must, must_not, exists, not_exists = self._make_split_filter_queries()
        self.search = self._get_or_create_search().post_filter(
            self._make_bool_query(
                must=must + exists,
                must_not=must_not + not_exists
            )
        )

    def add_source(self):
        self.search = self._get_or_create_search().extra(
            **{
                _SOURCE: self._get_return_fields()
            }
        )

    def add_slice(self):
        '''
        If limit=all or limit > MAX_ES_RESULTS_WINDOW we return
        default slice for the aggregations/total and scan over results
        in response mixin to_graph method.
        '''
        end = self._get_bounded_int_limit_value_or_default()
        self.search = self._get_or_create_search()[:end]

    def build_query(self):
        '''
        Public method to be implemented by children.
        '''
        raise NotImplementedError


class BasicSearchQueryFactory(AbstractQueryFactory):

    def __init__(self, params_parser, *args, **kwargs):
        super().__init__(params_parser, *args, **kwargs)

    def build_query(self):
        self.validate_item_types()
        self.add_query_string_query()
        self.add_filters()
        self.add_post_filters()
        self.add_source()
        self.add_slice()
        return self.search


class BasicSearchQueryFactoryWithFacets(BasicSearchQueryFactory):

    def __init__(self, params_parser, *args, **kwargs):
        super().__init__(params_parser, *args, **kwargs)

    def build_query(self):
        super().build_query()
        self.add_aggregations_and_aggregation_filters()
        return self.search


class BasicReportQueryFactoryWithFacet(BasicSearchQueryFactoryWithFacets):
    '''
    Like BasicSearchQueryFactoryWithFacets but makes sure single item type
    without subtypes is specified.
    '''

    def __init__(self, params_parser, *args, **kwargs):
        super().__init__(params_parser, *args, **kwargs)

    @assert_one_returned(error_message='Report view requires specifying a single type:')
    def _get_item_types(self):
        return super()._get_item_types()

    @assert_one_or_none_returned(error_message='Report view requires a type with no child types:')
    def validate_item_type_subtypes(self):
        return self._get_subtypes_for_item_type(
            self.params_parser.get_one_value(
                params=self._get_item_types()
            )
        )

    def build_query(self):
        self.validate_item_type_subtypes()
        return super().build_query()
