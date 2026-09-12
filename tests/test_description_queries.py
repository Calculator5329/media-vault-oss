"""Natural query wording must not bypass object/color association."""
import unittest
from src.descriptions import matches


def scene(color='blue'):
    return {'caption':f'A {color} sports car beside a red tree with advanced electronics.',
            'category':'photograph','objects':[{'name':'sports car','color':color},{'name':'tree','color':'red'}]}


class DescriptionQueryTests(unittest.TestCase):
    def test_articles_and_regular_plurals_keep_color_on_the_object(self):
        for query in ('a red car','the red cars','photos of a red car','red sports car'):
            self.assertFalse(matches(scene('blue'),query),query)
            self.assertTrue(matches(scene('red'),query),query)

    def test_whole_words_avoid_van_matching_advanced(self):
        self.assertFalse(matches(scene(),'van'))
        self.assertTrue(matches(scene(),'electronics'))
        self.assertTrue(matches(scene(),'sports cars'))

    def test_separate_color_clauses_each_require_their_object(self):
        self.assertTrue(matches(scene(),'blue car near a red tree'))
        self.assertFalse(matches(scene(),'red car near a blue tree'))
        self.assertTrue(matches(scene(),'show me a blue car'))

    def test_empty_browse_and_punctuation_query_are_distinct(self):
        self.assertTrue(matches(scene(),''))
        self.assertFalse(matches(scene(),'???'))
