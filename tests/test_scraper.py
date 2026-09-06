import io
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quizplease.jsobj import JsParseError, parse_nuxt_payload
from quizplease.scraper import (
    _parse_block_fields, _parse_datetime, extract_nuxt_state,
    normalize_game, parse_results_table, results_to_csv_rows,
)
from quizplease.xlsx import read_first_sheet

PAYLOAD = (
    '(function(a,b,c,d){return {data:{game:{status:"ok",data:{id:"abc",title:b,'
    'game_number:"3",date:"01.09.2026 19:30",status:d,price:15,current_price:"15",'
    'pay_method:1,city_id:158,country_id:30,place:{id:1,title:c,address:"Somewhere",'
    'lat:40.5,lon:49.9},template:{id:180,title:b,game_level:"medium"},'
    'block_with_text:"\\u003Cp\\u003E\\u003Cstrong\\u003EТема\\u003C\\u002Fstrong\\u003E: '
    'видеоигры\\u003C\\u002Fp\\u003E",result:{table:a,photos:[]},team_count:8,came_teams:8,'
    'all_registered_peoples:56,came_peoples:0,max_participants:10,is_championship:false}}},'
    'pinia:{location:{city:{id:"158",name:"Баку",slug:"baku"},'
    'country:{id:"30",name:"Азербайджан",currency:"₼"}}}}}'
    '(null,"[видеоигры] BAKU","Paulaner",4))'
)

RESULT_ROWS = [
    ["Место", "Ранг", "Название команды", "Итого", "Раунд 1", "Раунд 2"],
    ["1", "unattainable", "Noldor", "52", "5", "6.5"],
    ["2", "novich", "Ванси\t", "34", "3", "4"],
    ["", "", "", "", "", ""],
]


class JsObjTest(unittest.TestCase):
    def test_resolves_aliases_and_escapes(self):
        state = parse_nuxt_payload(PAYLOAD)
        game = state["data"]["game"]["data"]
        self.assertEqual(game["title"], "[видеоигры] BAKU")
        self.assertEqual(game["place"]["title"], "Paulaner")
        self.assertEqual(game["status"], 4)
        self.assertIsNone(game["result"]["table"])
        self.assertIn("<strong>Тема</strong>", game["block_with_text"])

    def test_literal_forms(self):
        value = parse_nuxt_payload(
            '(function(a){return {t:true,f:false,n:null,u:void 0,neg:-1.5,exp:1e3,'
            'arr:[1,a,[2]],"quoted key":a}}("x"))'
        )
        self.assertEqual(value, {
            "t": True, "f": False, "n": None, "u": None, "neg": -1.5,
            "exp": 1000.0, "arr": [1, "x", [2]], "quoted key": "x",
        })

    def test_unwrapped_payload(self):
        self.assertEqual(parse_nuxt_payload('{data:{game:null}}'), {"data": {"game": None}})

    def test_unknown_identifier_is_an_error(self):
        with self.assertRaises(JsParseError):
            parse_nuxt_payload('(function(a){return {x:zz}}("v"))')


class NormalizeTest(unittest.TestCase):
    def setUp(self):
        self.record = normalize_game(parse_nuxt_payload(PAYLOAD), "https://example/game/abc")

    def test_core_fields(self):
        self.assertEqual(self.record["id"], "abc")
        self.assertEqual(self.record["full_title"], "[видеоигры] BAKU 3")
        self.assertEqual(self.record["date"], "2026-09-01T19:30:00")
        self.assertEqual(self.record["status"], "finished")
        self.assertEqual(self.record["pay_method"], "cash")
        self.assertEqual(self.record["price"], 15)
        self.assertEqual(self.record["currency"], "₼")
        self.assertEqual(self.record["city"], {"id": 158, "name": "Баку", "slug": "baku"})
        self.assertEqual(self.record["format"], {"Тема": "видеоигры"})

    def test_missing_game_raises(self):
        from quizplease.scraper import ScrapeError
        with self.assertRaises(ScrapeError):
            normalize_game({"data": {"game": {"data": None}}}, "u")

    def test_extract_requires_payload(self):
        from quizplease.scraper import ScrapeError
        with self.assertRaises(ScrapeError):
            extract_nuxt_state("<html><body>no state here</body></html>")

    def test_extract_from_script_block(self):
        html = "<html><script>window.__NUXT__=%s;</script></html>" % PAYLOAD
        self.assertEqual(extract_nuxt_state(html)["data"]["game"]["status"], "ok")


class ResultsTest(unittest.TestCase):
    def test_parses_rows(self):
        results = parse_results_table(RESULT_ROWS)
        self.assertEqual(len(results), 2)          # blank row dropped
        self.assertEqual(results[0]["team"], "Noldor")
        self.assertEqual(results[0]["total"], 52)  # int, not 52.0
        self.assertEqual(results[0]["rounds"], {"round_1": 5, "round_2": 6.5})
        self.assertEqual(results[0]["rank_title"], "Недосягаемые")
        self.assertEqual(results[1]["team"], "Ванси")  # trailing tab stripped

    def test_csv_rows(self):
        record = normalize_game(parse_nuxt_payload(PAYLOAD), "u")
        record["results"] = parse_results_table(RESULT_ROWS)
        rows = list(results_to_csv_rows(record))
        self.assertEqual(rows[0][-2:], ["round_1", "round_2"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][6], "Noldor")

    def test_empty_table(self):
        self.assertEqual(parse_results_table([]), [])


class HelpersTest(unittest.TestCase):
    def test_datetime_formats(self):
        self.assertEqual(_parse_datetime("01.09.2026 19:30"), "2026-09-01T19:30:00")
        self.assertEqual(_parse_datetime("01.09.2026"), "2026-09-01T00:00:00")
        self.assertIsNone(_parse_datetime("not a date"))
        self.assertIsNone(_parse_datetime(None))

    def test_block_fields(self):
        fields = _parse_block_fields(
            "<p><strong>Формат</strong>: 7 раундов</p><p><br></p><p>free text</p>"
        )
        self.assertEqual(fields, {"Формат": "7 раундов"})


class XlsxTest(unittest.TestCase):
    def _workbook(self):
        sheet = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2"><v>1</v></c><c r="C2" t="inlineStr"><is><t>gap</t></is></c></row>'
            '</sheetData></worksheet>'
        )
        strings = (
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si><t>Место</t></si><si><t>Итого</t></si></sst>'
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
            archive.writestr("xl/sharedStrings.xml", strings)
        buffer.seek(0)
        return buffer

    def test_reads_cells(self):
        rows = read_first_sheet(self._workbook())
        self.assertEqual(rows[0], ["Место", "Итого", None])
        self.assertEqual(rows[1], ["1", None, "gap"])  # column B skipped in the XML


if __name__ == "__main__":
    unittest.main()
