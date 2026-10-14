"""Normalized Dublin/remote filtering retains original employer evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from dashboard import store,rating
from dashboard.locations import normalize_location,classify_location


class LocationNormalizationTests(unittest.TestCase):
    def geo(self, location, model='', title='Data Engineer'):
        return normalize_location({'location':location,'work_model':model,'title':title})

    def test_dublin_postal_districts_county_and_provider_codes_share_a_city(self):
        for location in ('Dublin, D, Ireland','DUBLIN 2, D, Ireland','Dublin, Co. Dublin, Ireland',
                         'DUBLIN 24, Ireland','County Dublin, Ireland','Dublin, IE'):
            with self.subTest(location=location):
                geo=self.geo(location)
                self.assertEqual(geo['location_city'],'Dublin')
                self.assertEqual(geo['location_country'],'Ireland')
                self.assertEqual(geo['location_display'],'Dublin, Ireland')
                self.assertTrue(geo['dublin_remote'])

    def test_county_dublin_towns_are_included_but_other_onsite_cities_are_not(self):
        for location in ('Swords, D, Ireland','Sandyford, Co. Dublin, Ireland','Dun Laoghaire, Ireland'):
            self.assertTrue(self.geo(location)['location_is_dublin'])
        self.assertEqual(self.geo('Swords, D, Ireland')['location_display'],'Swords, Dublin, Ireland')
        for location in ('Cork, C, Ireland','Galway, G, Ireland','Bray, Co. Wicklow, Ireland'):
            self.assertFalse(self.geo(location,'Hybrid')['dublin_remote'])

    def test_foreign_dublin_and_northern_ireland_do_not_match(self):
        for location in ('Dublin, Ohio, United States','Dublin, CA','Dublin, OH, USA','Northern Ireland'):
            with self.subTest(location=location):
                geo=self.geo(location,'Remote')
                self.assertFalse(geo['location_is_dublin'])
                self.assertFalse(geo['dublin_remote'])
                self.assertEqual(classify_location({'location':location,'work_model':'Remote'})['location_status'],'outside_target')

    def test_remote_ireland_regions_and_unknown_locations_have_distinct_confirmation_scopes(self):
        for location,scope in [('Remote, Ireland','ireland'),('Cork, C, Ireland','ireland'),
                               ('Remote - Europe','check'),('EMEA','check'),('Worldwide','check'),
                               ('','unclear'),('Remote','unclear')]:
            geo=self.geo(location,'Remote')
            self.assertTrue(geo['is_remote'])
            self.assertTrue(geo['dublin_remote'])
            self.assertEqual(geo['remote_scope'],scope)
        self.assertEqual(self.geo('Remote, Ireland','Remote')['location_display'],'Remote · Ireland')
        self.assertEqual(self.geo('','Remote')['location_display'],'Remote · location unspecified')
        for location in ('Remote, United Kingdom, EMEA','Remote, Germany, EMEA','Remote, United States','Remote, Canada'):
            self.assertFalse(self.geo(location,'Remote')['dublin_remote'])

    def test_remote_titles_do_not_turn_onsite_or_hybrid_roles_into_remote_jobs(self):
        self.assertTrue(self.geo('Ireland',title='Data Engineer (Remote)')['is_remote'])
        self.assertTrue(self.geo('Ireland',title='Remote Data Engineer')['is_remote'])
        self.assertFalse(self.geo('Cork, Ireland','On-site','Remote Sensing Engineer')['is_remote'])
        self.assertFalse(self.geo('Cork, Ireland','Hybrid','Data Engineer - Remote')['is_remote'])
        self.assertFalse(self.geo('Remote - Europe','Hybrid')['dublin_remote'])
        self.assertFalse(self.geo('Remote - Europe','On-site')['dublin_remote'])
        self.assertFalse(self.geo('Cork, Ireland','Not Remote')['is_remote'])
        self.assertFalse(self.geo('Cork, Ireland',title='Data Engineer (Not Remote)')['is_remote'])

    def test_multiple_locations_keep_the_ireland_option_without_relabeling_foreign_cities(self):
        geo=self.geo('London, England; Dublin, Ireland')
        self.assertTrue(geo['location_is_dublin'])
        self.assertEqual(geo['location_display'],'Dublin, Ireland; London, United Kingdom')

    def test_display_normalization_preserves_source_tracking_and_rating_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'jobs.db';store.init(path)
            settings={'cv_text':'Python SQL'}
            with store.connect(path) as db:
                jid,_=store.upsert(db,{'title':'Engineer','location':'DUBLIN 2, D, Ireland','description':'SQL',
                                      'stage':'interview','notes':'Keep this'},tracking=True)
                job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
                key=rating.fingerprint(job,settings)
                db.execute("UPDATE jobs SET rating_status='complete',rating_hash=?,analysis=? WHERE id=?",(key,json.dumps({'score':8,'summary':'Match','strengths':['SQL'],'gaps':[]}),jid))
                actual=store.library(db)[0]
                self.assertEqual(actual['location_display'],'Dublin, Ireland')
                self.assertEqual(actual['location'],'DUBLIN 2, D, Ireland')
                self.assertEqual(actual['rating_status'],'complete')
                self.assertEqual(rating.fingerprint(actual,settings),key)
                self.assertEqual(actual['stage'],'interview')
                self.assertEqual(actual['notes'],'Keep this')
