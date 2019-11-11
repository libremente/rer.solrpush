# -*- coding: utf-8 -*-
from DateTime import DateTime
from lxml import etree
from plone import api
from plone.indexer.interfaces import IIndexableObject
from rer.solrpush import _
from zope.component import queryMultiAdapter

import logging
import pysolr
import requests
import six

logger = logging.getLogger(__name__)

DATE_FIELDS = [
    'created',
    'modified',
    'effective',
    'ModificationDate',
    'CreationDate',
]

ADDITIONAL_FIELDS = ['showinsearch', 'searchwords']

PATTERN = '''
+(Title:({base_value})^5 OR Description:({base_value})^2 OR SearchableText:({base_value}) OR searchwords:({base_value})^1000)
+(portal_type:Document^10 OR portal_type:*) +((portal_type:* -portal_type:Folder)^1000 OR portal_type:Folder^0.01) +((portal_type:* -portal_type:Circolare)^1000 OR portal_type:Circolare^0.01) +(*:* OR  searchwords:redazione^100)
+showinsearch:True
'''


def get_solr_connection():
    is_ready = api.portal.get_registry_record(
        'rer.solrpush.interfaces.settings.IRerSolrpushSettings.ready',
        default=False,
    )

    solr_url = api.portal.get_registry_record(
        'rer.solrpush.interfaces.settings.IRerSolrpushSettings.solr_url',
        default=False,
    )
    if not is_ready or not solr_url:
        return
    return pysolr.Solr(solr_url, always_commit=True)


def parse_date_as_datetime(value):
    """ Sistemiamo le date
    """
    if value:
        format = '%Y-%m-%dT%H:%M:%S'
        return value.asdatetime().strftime(format) + 'Z'
    return value


def parse_date_str(value):
    return parse_date_as_datetime(DateTime(value))


def init_solr_push():
    """Inizializza la voce di registro 'index_fields'

    Lo fa leggendo il file xml di SOLR.

    :param solr_url: [required] L'url a cui richiedere il file xml
    :type solr_url: string
    :returns: Empty String if everything's good
    :rtype: String
    """
    solr_url = api.portal.get_registry_record(
        'rer.solrpush.interfaces.settings.IRerSolrpushSettings.solr_url',
        default=False,
    )

    if solr_url:
        if not solr_url.endswith('/'):
            solr_url = solr_url + '/'
        try:
            respo = requests.get(solr_url + 'admin/file?file=schema.xml')
        except requests.exceptions.RequestException as err:
            ErrorMessage = 'Connection problem:\n{0}'.format(err)
            return ErrorMessage
        if respo.status_code != 200:
            ErrorMessage = 'Problems fetching schema:\n{0}\n{1}'.format(
                respo.status_code, respo.reason
            )
            return ErrorMessage

        root = etree.fromstring(respo.content)
        chosen_fields = [
            extract_field_name(x) for x in root.findall('.//field')
        ]
        api.portal.set_registry_record(
            'rer.solrpush.interfaces.settings.IRerSolrpushSettings.index_fields',
            chosen_fields,
        )

        api.portal.set_registry_record(
            'rer.solrpush.interfaces.settings.IRerSolrpushSettings.ready', True
        )

        return ''

    return _('No SOLR url provided')


def extract_field_name(node):
    name = node.get('name')
    if six.PY2:
        name = unicode(name)  # noqa
    return name


def create_index_dict(item):
    """ Restituisce un dizionario pronto per essere 'mandato' a SOLR per
    l'indicizzazione.
    """

    index_fields = api.portal.get_registry_record(
        'rer.solrpush.interfaces.settings.IRerSolrpushSettings.index_fields',
        default=False,
    )

    catalog = api.portal.get_tool(name='portal_catalog')
    adapter = queryMultiAdapter((item, catalog), IIndexableObject)

    index_me = {}

    for field in index_fields:
        if six.PY2:
            field = field.encode('ascii')
        value = getattr(adapter, field, None)
        if not value:
            continue
        if callable(value):
            value = value()

        if isinstance(value, DateTime):
            value = parse_date_as_datetime(value)
        else:
            if field in DATE_FIELDS:
                value = parse_date_str(value)
        index_me[field] = value

    for field in ADDITIONAL_FIELDS:
        value = getattr(item, field, None)
        if value is not None:
            index_me[field] = value
    index_me['site_name'] = api.portal.get().getId()
    return index_me


def push_to_solr(item):
    """
    Perform push to solr
    """
    solr = get_solr_connection()

    if not solr:
        logger.error('Unable to push to solr. Configuration is incomplete.')
        return
    index_me = create_index_dict(item)
    try:
        solr.add([index_me])
        # message = _(
        #     'content_indexed_success',
        #     default=u'Content correctly indexed on SOLR',
        # )
        # api.portal.show_message(message=message, request=item.REQUEST)
    except pysolr.SolrError as err:
        logger.error(err)
        message = _(
            'content_indexed_error',
            default=u'There was a problem indexing this content. Please '
            'contact site administrator.',
        )
        api.portal.show_message(
            message=message, request=item.REQUEST, type='error'
        )


def remove_from_solr(uid):
    """
    Perform remove item from solr
    """
    solr = get_solr_connection()
    portal = api.portal.get()
    if not solr:
        logger.error('Unable to push to solr. Configuration is incomplete.')
        return
    try:
        solr.delete(q='UID:{}'.format(uid), commit=True)
    except (pysolr.SolrError, TypeError) as err:
        logger.error(err)
        message = _(
            'content_indexed_error',
            default=u'There was a problem removing this content from SOLR. '
            ' Please contact site administrator.',
        )
        api.portal.show_message(
            message=message, request=portal.REQUEST, type='error'
        )


def reset_solr():
    solr = get_solr_connection()
    if not solr:
        logger.error('Unable to push to solr. Configuration is incomplete.')
        return
    solr.delete(q='*:*')


def search(query, fl=''):
    solr = get_solr_connection()
    q, fq = generate_query(query)
    if not solr:
        logger.error('Unable to push to solr. Configuration is incomplete.')
        return
    additional_parameters = {
        'fq': fq,
        'facet': 'true',
        'facet.field': ['Subject', 'portal_type'],
        'start': query.get('b_start', 0),
        'rows': query.get('b_size', 20),
    }
    if fl:
        additional_parameters['fl'] = fl
    return solr.search(q=q, **additional_parameters)


def generate_query(query):
    q = ''
    fq = ['site_name:{}'.format(api.portal.get().getId())]
    pattern = ''  # TODO
    if not pattern:
        for index, value in query.items():
            if index == 'SearchableText':
                q = 'SearchableText:{}'.format(value)
            elif index == '*':
                q = '*:{}'.format(value)
            else:
                fq.append('{index}:{value}'.format(index=index, value=value))
    return q, fq


# fq=+portal_type:(BookCrossing+OR+AdsCategory+OR+WildcardVideo+OR+File+OR+Image+OR+Circolare+OR+LinkNormativa+OR+Collection+OR+Document+OR+WildcardAudio+OR+BulletinBoard+OR+Link+OR+Advertisement+OR+Folder+OR+"News+Item"+OR+Aforisma+OR+Event)
# fq=+allowedRolesAndUsers:(user$Cecchi_A+OR+Member+OR+Manager+OR+Authenticated+OR+user$Administrators+OR+user$AuthenticatedUsers+OR+Anonymous)
# rows=20
# facet.field=Subjec
# facet.field=portal_type
# enableElevation=false
# facet=true
# start=0
# q=+path_parents:"\/orma"++(Title:(regione)^5+OR+Description:(regione)^2+OR+SearchableText:(regione)+OR+searchwords:(regione)^1000)+%0D%0A+(portal_type:Document^10+OR+portal_type:*)++(portal_type:*+-portal_type:Folder)^1000+OR+portal_type:Folder^0.01)++(portal_type:*+-portal_type:Circolare)^1000+OR+portal_type:Circolare^0.01)++(*:*+OR++searchwords:redazione^100)%0D%0A+showinsearch:True
# fl=*+score+[elevated]
