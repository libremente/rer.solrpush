# -*- coding: utf-8 -*-
from rer.solrpush.solr import search
from zope.interface import implementer
from zope.schema.interfaces import IVocabularyFactory
from zope.schema.vocabulary import SimpleVocabulary, SimpleTerm


@implementer(IVocabularyFactory)
class AvailableSitesVocabularyFactory(object):
    @property
    def terms(self):
        solr_results = search(
            query={'*': '*'}, fl='UID', facets=True, facet_fields='site_name'
        )
        # if solr_results.get('error', False):
        #     return []
        facets = solr_results.facets['facet_fields'].get('site_name', [])
        if not facets:
            return []
        terms = []
        for facet in facets:
            for key in facet.keys():
                terms.append(SimpleTerm(value=key, token=key, title=key))
        return terms

    def __call__(self, context):
        return SimpleVocabulary(self.terms)


AvailableSitesVocabulary = AvailableSitesVocabularyFactory()