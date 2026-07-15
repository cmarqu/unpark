import unittest

from unpark import parse_welcome

SAMPLE = """\
---
name: photo-globe
tagline: Photos on a spinning 3D globe
status: parked
updated: 2026-06-02
---

## What is this

A small web app. Two sentences of prose.

## Goals

- [x] Read EXIF geo
- [ ] Cluster markers

## Recipes

### demo

Serves the globe with sample data.

- background: true
- dir: web
- url: http://localhost:5173

```sh
python3 -m http.server 5173
```

### build

```sh
mkdir -p dist
cp -r web/* dist/
```

```sh
echo second step
```

## Distribute

Upload dist/ somewhere.
"""


class TestFrontMatter(unittest.TestCase):
    def test_parses_key_value_front_matter(self):
        w = parse_welcome(SAMPLE)
        self.assertEqual(w.meta["name"], "photo-globe")
        self.assertEqual(w.meta["status"], "parked")
        self.assertEqual(w.meta["updated"], "2026-06-02")

    def test_missing_front_matter_is_tolerated(self):
        w = parse_welcome("## What is this\n\nJust prose.\n")
        self.assertEqual(w.meta, {})
        self.assertEqual(w.sections[0].title, "What is this")


class TestSections(unittest.TestCase):
    def test_sections_in_order_with_bodies(self):
        w = parse_welcome(SAMPLE)
        titles = [s.title for s in w.sections]
        self.assertEqual(
            titles, ["What is this", "Goals", "Recipes", "Distribute"]
        )
        self.assertIn("A small web app.", w.sections[0].body)

    def test_goal_progress_counts_checkboxes(self):
        w = parse_welcome(SAMPLE)
        self.assertEqual(w.goals_done, 1)
        self.assertEqual(w.goals_total, 2)


class TestRecipes(unittest.TestCase):
    def test_recipe_names(self):
        w = parse_welcome(SAMPLE)
        self.assertEqual([r.name for r in w.recipes], ["demo", "build"])

    def test_recipe_description_is_prose_before_metadata(self):
        w = parse_welcome(SAMPLE)
        self.assertEqual(w.recipes[0].description,
                         "Serves the globe with sample data.")

    def test_recipe_metadata_bullets(self):
        w = parse_welcome(SAMPLE)
        demo = w.recipes[0]
        self.assertTrue(demo.background)
        self.assertEqual(demo.dir, "web")
        self.assertEqual(demo.url, "http://localhost:5173")

    def test_recipe_defaults(self):
        w = parse_welcome(SAMPLE)
        build = w.recipes[1]
        self.assertFalse(build.background)
        self.assertIsNone(build.dir)
        self.assertIsNone(build.url)
        self.assertIsNone(build.needs)

    def test_multiple_fences_become_sequential_steps(self):
        w = parse_welcome(SAMPLE)
        self.assertEqual(w.recipes[1].steps,
                         ["mkdir -p dist\ncp -r web/* dist/",
                          "echo second step"])

    def test_needs_metadata(self):
        text = SAMPLE + "\n### publish\n\n- needs: build\n\n```sh\nrsync dist/ srv:\n```\n"
        # recipes appended after Distribute still belong to Recipes? No —
        # only ### under ## Recipes count. This one is under Distribute.
        w = parse_welcome(text)
        self.assertEqual([r.name for r in w.recipes], ["demo", "build"])

    def test_needs_parsed_when_inside_recipes_section(self):
        text = SAMPLE.replace(
            "### build",
            "### publish\n\n- needs: build\n\n```sh\nrsync\n```\n\n### build",
        )
        w = parse_welcome(text)
        publish = [r for r in w.recipes if r.name == "publish"][0]
        self.assertEqual(publish.needs, "build")

    def test_unknown_metadata_keys_are_kept(self):
        text = SAMPLE.replace("- dir: web", "- dir: web\n- flavor: spicy")
        w = parse_welcome(text)
        self.assertEqual(w.recipes[0].meta.get("flavor"), "spicy")


if __name__ == "__main__":
    unittest.main()
