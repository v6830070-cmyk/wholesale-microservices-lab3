"""Русская страница Swagger (/docs): подменяет надписи интерфейса, в том числе кнопку Authorize."""
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

TRANSLATE_SCRIPT = """
<script>
(function () {
  var EXACT = {
    "Authorize": "Авторизация",
    "Available authorizations": "Доступные способы авторизации",
    "Value:": "Значение:",
    "Logout": "Выйти",
    "Close": "Закрыть",
    "Try it out": "Попробовать",
    "Cancel": "Отмена",
    "Execute": "Выполнить",
    "Clear": "Очистить",
    "Parameters": "Параметры",
    "No parameters": "Нет параметров",
    "Responses": "Ответы",
    "Code": "Код",
    "Description": "Описание",
    "Links": "Ссылки",
    "No links": "Нет ссылок",
    "Request body": "Тело запроса",
    "Example Value": "Пример значения",
    "Schema": "Схема",
    "Schemas": "Схемы",
    "Server response": "Ответ сервера",
    "Response body": "Тело ответа",
    "Response headers": "Заголовки ответа",
    "Request URL": "Адрес запроса",
    "Details": "Подробности",
    "Successful Response": "Успешный ответ",
    "Validation Error": "Ошибка валидации",
    "Media type": "Тип данных",
    "Download": "Скачать"
  };
  var PREFIXES = [["Contact ", "Контакт "]];
  var SKIP = {SCRIPT: 1, STYLE: 1, PRE: 1, CODE: 1, TEXTAREA: 1, INPUT: 1};

  function translateNode(node) {
    if (node.nodeType === 3) {
      var text = node.nodeValue;
      var key = text.trim();
      if (!key) { return; }
      if (EXACT.hasOwnProperty(key)) {
        node.nodeValue = text.replace(key, EXACT[key]);
        return;
      }
      for (var i = 0; i < PREFIXES.length; i++) {
        if (key.indexOf(PREFIXES[i][0]) === 0) {
          node.nodeValue = text.replace(PREFIXES[i][0], PREFIXES[i][1]);
          return;
        }
      }
    } else if (node.nodeType === 1 && !SKIP[node.tagName]) {
      for (var c = node.firstChild; c; c = c.nextSibling) { translateNode(c); }
    }
  }

  var busy = false;
  function run() {
    if (busy) { return; }
    busy = true;
    translateNode(document.body);
    busy = false;
  }
  new MutationObserver(run).observe(document.body, {childList: true, subtree: true, characterData: true});
  run();
})();
</script>
"""


def setup_docs_ru(app: FastAPI) -> None:
    """Подключает /docs с русскими надписями. Приложение нужно создавать с docs_url=None."""

    @app.get("/docs", include_in_schema=False)
    async def russian_docs() -> HTMLResponse:
        page = get_swagger_ui_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=f"{app.title} — документация",
            swagger_ui_parameters={"persistAuthorization": True},
        )
        html = page.body.decode("utf-8").replace("</body>", TRANSLATE_SCRIPT + "</body>")
        return HTMLResponse(html)
