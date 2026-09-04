(function () {
  "use strict";

  /*
   * This is intentionally a small, dependency-free highlighter. Code is
   * authored in the content repositories and already arrives escaped in the
   * page, so adding a runtime such as Prism would make every reading page
   * heavier and would require another CSP-sensitive asset. The scanner below
   * only creates text nodes and spans; it never evaluates or injects source as
   * HTML. If a language is unknown, the original text is left untouched.
   */
  var LANGUAGE_ALIASES = {
    bash: "bash",
    console: "plaintext",
    css: "css",
    docker: "docker",
    dockerfile: "docker",
    dotenv: "bash",
    env: "bash",
    html: "markup",
    "html+django": "markup",
    ini: "ini",
    java: "javascript",
    javascript: "javascript",
    jinja: "markup",
    json: "json",
    markdown: "markdown",
    md: "markdown",
    mermaid: "mermaid",
    plaintext: "plaintext",
    postgres: "sql",
    postgresql: "sql",
    powershell: "powershell",
    py: "python",
    python: "python",
    sh: "bash",
    shell: "bash",
    sql: "sql",
    text: "plaintext",
    toml: "ini",
    ts: "javascript",
    typescript: "javascript",
    yaml: "yaml",
    yml: "yaml",
    zsh: "bash",
  };

  function wordSet(words) {
    var result = Object.create(null);
    words.split(" ").forEach(function (word) {
      if (word) result[word] = true;
    });
    return result;
  }

  var LANGUAGE_CONFIG = {
    bash: {
      lineComments: ["#"],
      keywords: wordSet(
        "case coproc do done elif else esac exit export fi for function if in select then time until while"
      ),
      constants: wordSet("false null true"),
      variables: true,
    },
    css: {
      blockComments: true,
      keywords: wordSet("@import @media @supports @keyframes"),
      properties: true,
    },
    docker: {
      lineComments: ["#"],
      keywords: wordSet(
        "ADD ARG CMD COPY ENTRYPOINT ENV EXPOSE FROM HEALTHCHECK LABEL MAINTAINER ONBUILD RUN SHELL STOPSIGNAL USER VOLUME WORKDIR"
      ),
      constants: wordSet("true false"),
    },
    ini: {
      lineComments: ["#", ";"],
      properties: true,
    },
    javascript: {
      lineComments: ["//"],
      blockComments: true,
      keywords: wordSet(
        "as async await break case catch class const continue debugger default delete do else export extends finally for from function get if implements import in instanceof interface let new of package private protected public return set static super switch this throw try typeof undefined var void while with yield"
      ),
      types: wordSet("Array Boolean Date Error Function Map Number Object Promise RegExp Set String Symbol WeakMap"),
      constants: wordSet("false null true"),
      variables: true,
      properties: true,
    },
    json: {
      constants: wordSet("false null true"),
      properties: true,
    },
    markdown: {
      markdown: true,
    },
    markup: {
      blockComments: true,
      markup: true,
    },
    mermaid: {
      lineComments: ["%%"],
      keywords: wordSet(
        "classDef classDiagram click direction end erDiagram flowchart graph gantt journey mindmap participant sequenceDiagram stateDiagram subgraph timeline"
      ),
      constants: wordSet("false true"),
    },
    powershell: {
      lineComments: ["#"],
      blockComments: true,
      keywords: wordSet(
        "begin break catch class continue data define do dynamicparam else elseif end exit filter finally for foreach from function if in param process return switch throw trap try until using while"
      ),
      constants: wordSet("false null true"),
      variables: true,
    },
    python: {
      lineComments: ["#"],
      tripleQuotes: true,
      keywords: wordSet(
        "and as assert async await break case class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try while with yield"
      ),
      types: wordSet("bool bytes dict float frozenset int list object set str tuple"),
      constants: wordSet("False None True"),
      functions: wordSet("len open print range zip"),
    },
    sql: {
      lineComments: ["--"],
      blockComments: true,
      caseInsensitive: true,
      keywords: wordSet(
        "all alter and as asc begin between by case cast check column commit constraint create cross current delete desc distinct do drop else end exists explain from full grant group having in index inner insert into is join left limit not null on or order outer primary references right rollback select set table then to truncate union unique update using values view when where with"
      ),
      types: wordSet("bigint boolean date decimal integer json numeric real serial text timestamp uuid varchar"),
      constants: wordSet("false null true"),
      functions: wordSet("avg count max min sum"),
      properties: true,
    },
    yaml: {
      lineComments: ["#"],
      constants: wordSet("false null true yes no"),
      properties: true,
    },
  };

  var OPERATOR_PATTERN = /^(?:===|!==|>>>|=>|==|!=|<=|>=|&&|\|\||\?\?|\*\*|\/\*|\*\/|\/\/|::|:=|->|<-|\+=|-=|\*=|\/=|%=|\.\.\.|[-+*/%=<>!&|^~?:])/;
  var NUMBER_PATTERN = /^(?:0[xX][0-9a-fA-F]+|0[bB][01]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)/;
  var IDENTIFIER_PATTERN = /^[A-Za-z_$][A-Za-z0-9_$]*/;

  /* The language token comes from a course body written in a public repository,
     so it is untrusted text.  Reading it with a plain lookup answered inherited
     names -- a fence marked ```constructor resolved to `Object` and the block
     ended up labelled "function Object() { [native code] }" -- so both maps are
     read by own property only and an unknown name stays the word it was. */
  function ownProperty(map, key) {
    return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : undefined;
  }

  function normalizedLanguage(code) {
    var classes = String(code.className || "").split(/\s+/);
    for (var i = 0; i < classes.length; i += 1) {
      if (classes[i].indexOf("language-") !== 0) continue;
      var raw = classes[i].slice("language-".length).toLowerCase();
      return ownProperty(LANGUAGE_ALIASES, raw) || raw;
    }
    return "plaintext";
  }

  function startsWithAt(source, value, index) {
    return source.slice(index, index + value.length) === value;
  }

  function appendToken(tokens, text, type) {
    if (!text) return;
    var previous = tokens[tokens.length - 1];
    if (previous && previous.type === type) {
      previous.text += text;
      return;
    }
    tokens.push({ text: text, type: type || "" });
  }

  function readLineComment(source, index) {
    var end = source.indexOf("\n", index);
    return end === -1 ? source.length : end;
  }

  function readBlockComment(source, index) {
    var end = source.indexOf("*/", index + 2);
    return end === -1 ? source.length : end + 2;
  }

  function readQuoted(source, index, config) {
    var quote = source.charAt(index);
    var triple = config.tripleQuotes && source.slice(index, index + 3) === quote.repeat(3);
    var delimiter = triple ? quote.repeat(3) : quote;
    var cursor = index + delimiter.length;

    while (cursor < source.length) {
      if (startsWithAt(source, delimiter, cursor)) return cursor + delimiter.length;
      if (!triple && source.charAt(cursor) === "\n") return cursor;
      if (source.charAt(cursor) === "\\") {
        cursor += 2;
        continue;
      }
      /* SQL and YAML escape a quote by doubling it. */
      if (source.charAt(cursor) === quote && source.charAt(cursor + 1) === quote) {
        cursor += 2;
        continue;
      }
      cursor += 1;
    }
    return source.length;
  }

  function readMarkupTag(source, index) {
    var quote = "";
    var cursor = index;
    while (cursor < source.length) {
      var character = source.charAt(cursor);
      if (quote) {
        if (character === "\\") {
          cursor += 2;
          continue;
        }
        if (character === quote) quote = "";
      } else if (character === '"' || character === "'") {
        quote = character;
      } else if (character === ">") {
        return cursor + 1;
      } else if (character === "\n") {
        return index;
      }
      cursor += 1;
    }
    return index;
  }

  function readMarkupTokens(tokens, source) {
    var cursor = 0;
    var tagName = /^\s*(?:<\/?|<!)\s*([A-Za-z][A-Za-z0-9:_-]*)/;
    var attributeName = /^[A-Za-z_:][A-Za-z0-9:_.-]*/;

    while (cursor < source.length) {
      var tag = source.slice(cursor);
      var nameMatch = tag.match(tagName);
      if (!nameMatch) {
        appendToken(tokens, source.charAt(cursor), "");
        cursor += 1;
        continue;
      }

      var start = cursor;
      var end = readMarkupTag(source, cursor);
      if (end === cursor) {
        appendToken(tokens, source.charAt(cursor), "");
        cursor += 1;
        continue;
      }

      var tagText = source.slice(start, end);
      var tagCursor = 0;
      var tagNameStart = tagText.indexOf(nameMatch[1]);
      appendToken(tokens, tagText.slice(tagCursor, tagNameStart), "punctuation");
      appendToken(tokens, nameMatch[1], "tag");
      tagCursor = tagNameStart + nameMatch[1].length;

      while (tagCursor < tagText.length) {
        var rest = tagText.slice(tagCursor);
        var attribute = rest.match(attributeName);
        if (attribute) {
          appendToken(tokens, attribute[0], "property");
          tagCursor += attribute[0].length;
          continue;
        }
        var tagQuote = tagText.charAt(tagCursor);
        if (tagQuote === '"' || tagQuote === "'") {
          var quotedEnd = readQuoted(tagText, tagCursor, {});
          appendToken(tokens, tagText.slice(tagCursor, quotedEnd), "string");
          tagCursor = quotedEnd;
          continue;
        }
        var punctuation = tagText.charAt(tagCursor);
        appendToken(tokens, punctuation, /[=/>]/.test(punctuation) ? "punctuation" : "");
        tagCursor += 1;
      }
      cursor = end;
    }
  }

  function tokenTypeForWord(source, start, end, word, config) {
    var comparable = config.caseInsensitive ? word.toLowerCase() : word;
    if (config.keywords && config.keywords[comparable]) return "keyword";
    if (config.types && config.types[comparable]) return "type";
    if (config.constants && config.constants[comparable]) return "constant";
    if (config.functions && config.functions[comparable]) return "function";
    if (config.variables && word.charAt(0) === "$") return "variable";

    var before = source.slice(0, start).match(/\.?\s*$/);
    var after = source.slice(end).match(/^\s*/)[0].length;
    var next = source.charAt(end + after);
    if (config.properties && (next === ":" || (before && before[0].charAt(0) === "."))) {
      return "property";
    }
    if (next === "(") return "function";
    if (/^[A-Z][A-Z0-9_]*$/.test(word)) return "constant";
    return "";
  }

  function tokenize(source, config) {
    var tokens = [];
    var cursor = 0;
    var lineStart = true;

    while (cursor < source.length) {
      if (config.markup && source.charAt(cursor) === "<") {
        var tagEnd = readMarkupTag(source, cursor);
        if (tagEnd > cursor) {
          readMarkupTokens(tokens, source.slice(cursor, tagEnd));
          cursor = tagEnd;
          lineStart = false;
          continue;
        }
      }

      if (config.markdown && lineStart) {
        var heading = source.slice(cursor).match(/^#{1,6}(?=\s)/);
        if (heading) {
          appendToken(tokens, heading[0], "heading");
          cursor += heading[0].length;
          lineStart = false;
          continue;
        }
      }

      var comment = false;
      if (config.lineComments) {
        for (var commentIndex = 0; commentIndex < config.lineComments.length; commentIndex += 1) {
          if (startsWithAt(source, config.lineComments[commentIndex], cursor)) {
            var lineCommentEnd = readLineComment(source, cursor);
            appendToken(tokens, source.slice(cursor, lineCommentEnd), "comment");
            cursor = lineCommentEnd;
            comment = true;
            break;
          }
        }
      }
      if (comment) continue;

      if (config.blockComments && startsWithAt(source, "/*", cursor)) {
        var blockEnd = readBlockComment(source, cursor);
        appendToken(tokens, source.slice(cursor, blockEnd), "comment");
        lineStart = source.charAt(blockEnd - 1) === "\n";
        cursor = blockEnd;
        continue;
      }

      var character = source.charAt(cursor);
      if (character === '"' || character === "'" || character === "`") {
        var stringEnd = readQuoted(source, cursor, config);
        appendToken(tokens, source.slice(cursor, stringEnd), "string");
        lineStart = source.charAt(stringEnd - 1) === "\n";
        cursor = stringEnd;
        continue;
      }

      if (/\s/.test(character)) {
        appendToken(tokens, character, "");
        lineStart = character === "\n";
        cursor += 1;
        continue;
      }

      var number = source.slice(cursor).match(NUMBER_PATTERN);
      if (number) {
        appendToken(tokens, number[0], "number");
        cursor += number[0].length;
        lineStart = false;
        continue;
      }

      var identifier = source.slice(cursor).match(IDENTIFIER_PATTERN);
      if (identifier) {
        appendToken(
          tokens,
          identifier[0],
          tokenTypeForWord(source, cursor, cursor + identifier[0].length, identifier[0], config)
        );
        cursor += identifier[0].length;
        lineStart = false;
        continue;
      }

      var operator = source.slice(cursor).match(OPERATOR_PATTERN);
      if (operator) {
        appendToken(tokens, operator[0], "operator");
        cursor += operator[0].length;
        lineStart = false;
        continue;
      }

      appendToken(tokens, character, /[{}[\]();,.]/.test(character) ? "punctuation" : "");
      lineStart = false;
      cursor += 1;
    }
    return tokens;
  }

  function appendHighlightedCode(code, language) {
    var config = ownProperty(LANGUAGE_CONFIG, language);
    if (!config || language === "plaintext") return;

    var source = code.textContent || "";
    if (!source) return;

    var fragment = document.createDocumentFragment();
    tokenize(source, config).forEach(function (token) {
      if (!token.type) {
        fragment.appendChild(document.createTextNode(token.text));
        return;
      }
      var span = document.createElement("span");
      span.className = "code-token-" + token.type;
      span.textContent = token.text;
      fragment.appendChild(span);
    });
    code.textContent = "";
    code.appendChild(fragment);
    code.classList.add("code-highlighted");
    code.setAttribute("data-code-language", language);
  }

  function setButtonReady(button) {
    button.textContent = "Copy";
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.removeAttribute("data-copy-state");
    button.classList.remove("is-copied", "is-error");
  }

  function selectCode(code) {
    try {
      var range = document.createRange();
      range.selectNodeContents(code);
      var selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    } catch (_selectionError) {
      /* Selecting is only a convenience fallback; the code remains readable. */
    }
  }

  function writeToClipboard(text) {
    if (
      typeof navigator === "undefined" ||
      !navigator.clipboard ||
      typeof navigator.clipboard.writeText !== "function"
    ) {
      return Promise.reject(new Error("clipboard-unavailable"));
    }
    try {
      return Promise.resolve(navigator.clipboard.writeText(text));
    } catch (_clipboardError) {
      return Promise.reject(_clipboardError);
    }
  }

  function showFailure(button, status, code) {
    button.textContent = "Try again";
    button.setAttribute("data-copy-state", "error");
    button.classList.add("is-error");
    button.disabled = false;
    button.removeAttribute("aria-busy");
    selectCode(code);
    status.textContent = "Could not copy code. Select and copy it manually.";
    window.setTimeout(function () {
      setButtonReady(button);
    }, 2500);
  }

  function enhanceCodeBlock(pre, index) {
    if (pre.closest(".code-block")) return;

    var code = pre.querySelector("code");
    if (!code || !pre.parentNode) return;

    var language = normalizedLanguage(code);
    appendHighlightedCode(code, language);
    pre.setAttribute("data-code-language", language);

    /* Samples wrap rather than scroll, so a long first line runs the full width
       of the block and the floating copy control would sit on top of it.  An
       empty float at the head of the `pre` shortens exactly the line boxes the
       control covers and nothing else: the sample keeps its own padding, the
       block never reflows when the control appears, and no reserved strip is
       drawn above the first line.  It lives outside `code`, so the copied text
       and the language runtime never see it. */
    var gutter = document.createElement("span");
    gutter.className = "code-block-gutter";
    gutter.setAttribute("aria-hidden", "true");
    pre.insertBefore(gutter, pre.firstChild);

    var frame = document.createElement("div");
    frame.className = "code-block";

    var button = document.createElement("button");
    button.className = "code-block-copy";
    button.type = "button";
    button.textContent = "Copy";
    button.setAttribute("aria-label", "Copy code");

    var status = document.createElement("span");
    status.className = "sr-only code-block-copy-status";
    status.id = "code-block-copy-status-" + String(index + 1);
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.setAttribute("aria-atomic", "true");
    button.setAttribute("aria-describedby", status.id);

    pre.parentNode.insertBefore(frame, pre);
    frame.appendChild(pre);
    frame.appendChild(button);
    frame.appendChild(status);

    button.addEventListener("click", function () {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.removeAttribute("data-copy-state");
      button.classList.remove("is-copied", "is-error");
      status.textContent = "Copying code";

      writeToClipboard(code.textContent || "").then(
        function () {
          button.textContent = "Copied";
          button.disabled = false;
          button.removeAttribute("aria-busy");
          button.setAttribute("data-copy-state", "copied");
          button.classList.add("is-copied");
          status.textContent = "Code copied to clipboard.";
          window.setTimeout(function () {
            setButtonReady(button);
          }, 2000);
        },
        function () {
          showFailure(button, status, code);
        }
      );
    });
  }

  function enhanceCodeBlocks(root) {
    (root || document).querySelectorAll(".prose pre").forEach(enhanceCodeBlock);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      enhanceCodeBlocks();
    });
  } else {
    enhanceCodeBlocks();
  }

  /* Dynamic prose can opt into the same enhancement without duplicating the
     scanner; already-enhanced blocks are skipped by enhanceCodeBlock. */
  window.enhanceCodeBlocks = enhanceCodeBlocks;
})();
