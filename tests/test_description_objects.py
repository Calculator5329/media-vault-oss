"""Accept modest model overrun without truncation or weakening object checks."""
import json
import unittest
from src import descriptions

class DescriptionObjectTests(unittest.TestCase):
    def value(self,count):
        return {'caption':'Synthetic objects.','category':'illustration','objects':[{'name':f'object {i}','color':'red'} for i in range(count)]}

    def test_all_objects_retained_through_bound(self):
        for count in (11,12,32):
            value=self.value(count)
            self.assertEqual(descriptions.parse(json.dumps(value)),value)

    def test_oversized_or_invalid_late_object_rejected(self):
        with self.assertRaises(ValueError):descriptions.parse(json.dumps(self.value(33)))
        value=self.value(12);value['objects'][-1]['name']=None
        with self.assertRaises(ValueError):descriptions.parse(json.dumps(value))
