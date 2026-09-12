import json,tempfile,unittest
from pathlib import Path
from src.basemap import simplify,rings,lines,build_detail,near

class BasemapTests(unittest.TestCase):
    def test_simplify_keeps_corners_and_drops_collinear_points(self):
        line=[[0,0],[1,0.0001],[2,0],[3,0],[3,3]]
        self.assertEqual(simplify(line,0.01),[[0,0],[3,0],[3,3]])
        self.assertEqual(simplify(line,0),line)
        self.assertEqual(simplify([[0,0],[1,1]],1),[[0,0],[1,1]])

    def test_rings_and_lines_round_and_drop_degenerate_shapes(self):
        poly={'type':'Polygon','coordinates':[[[0,0],[0.00001,0],[1,0],[1,1],[0,1],[0,0]]]}
        self.assertEqual(rings(poly,0.001),[[[0,0],[1,0],[1,1],[0,1],[0,0]]])
        self.assertEqual(rings({'type':'MultiPolygon','coordinates':[[[[0,0],[0,0],[0,0],[0,0]]]]},0.001),[])
        self.assertEqual(lines({'type':'MultiLineString','coordinates':[[[0,0],[0.5,0.0001],[1,0]],[[2,2],[2,2]]]},0.01),[[[0,0],[1,0]]])

    def test_detail_keeps_only_roads_near_recorded_places(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'ne';src.mkdir()
            roads={'type':'FeatureCollection','features':[
                {'type':'Feature','properties':{'type':'Road','min_zoom':7,'name':'Near road'},'geometry':{'type':'LineString','coordinates':[[-92.5,44.0],[-92.3,44.1]]}},
                {'type':'Feature','properties':{'type':'Road','min_zoom':7,'name':'Far road'},'geometry':{'type':'LineString','coordinates':[[10,50],[10.2,50.1]]}},
                {'type':'Feature','properties':{'type':'Ferry Route','min_zoom':7},'geometry':{'type':'LineString','coordinates':[[-92.5,44.0],[-92.4,44.0]]}}]}
            urban={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'area_sqkm':50,'min_zoom':8},'geometry':{'type':'Polygon','coordinates':[[[-92.5,44.0],[-92.4,44.0],[-92.4,44.1],[-92.5,44.0]]]}}]}
            (src/'ne_10m_roads.geojson').write_text(json.dumps(roads));(src/'ne_10m_urban_areas.geojson').write_text(json.dumps(urban))
            target=Path(tmp)/'detail.json';build_detail(src,target,[(44.0,-92.4)],reach=2)
            detail=json.loads(target.read_text())
            self.assertEqual([r['n'] for r in detail['roads']],['Near road']);self.assertEqual(len(detail['urban']),1)
        self.assertTrue(near((0,0,1,1),[(0.5,0.5)],0.1));self.assertFalse(near((0,0,1,1),[(5,5)],1))

if __name__=='__main__':unittest.main()
