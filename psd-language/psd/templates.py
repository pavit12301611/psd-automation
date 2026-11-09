"""Project templates for `psd new <dir> --template <name>`.

Starting from a compiling skeleton beats starting from a blank file —
for humans and for AI agents. Every template follows the house patterns
(try_* envelopes, argv-safe processes, stdlib imports) and passes
`psd check` the moment it is generated (the test suite proves it).
"""

from __future__ import annotations

_CLI = '''# {name} — CLI tool (args, files, recoverable errors)
# Run: psd run main.psd <input> <output>

fn main() {
    let a = args()
    if a.len() != 2 {
        print("usage: {name} <input> <output>")
        exit(2)
    }
    let r = try_read_file(a[0])
    if r["ok"] {
        write_file(a[1], str(r["value"]).upper())
        print("wrote", a[1])
    } else {
        print("cannot read", a[0], "-", str(r["error"]))
        exit(1)
    }
}
'''

_API = '''# {name} — API client (HTTP + json, failures are data)
# Run: API_KEY=... psd run main.psd

fn main() {
    let payload = {"prompt": "hello from PSD", "max_tokens": 16}
    let headers = {"Authorization": "Bearer " + env("API_KEY", "demo")}
    let r = http_request("POST", "https://api.example.com/v1/echo",
                         json_encode(payload), headers)
    if r["ok"] {
        let data = try_json_parse(str(r["body"]))
        if data["ok"] {
            print(json_encode(data["value"]))
        }
    } else {
        print("request failed with status", int(r["status"]))
    }
}
'''

_WEB = '''import "std/web.psd" as web

# {name} — web GUI (server-rendered, escaping components)
# Run: psd run main.psd  ->  http://127.0.0.1:8080

fn main() {
    let items: [str] = ["first task"]
    let port = http_listen(int(env("PSD_PORT", "8080")))
    print(str(port))
    while true {
        let req = http_accept()
        if req["path"] == "/" {
            let body = web.h1("{name}") + web.list_items(items) +
                web.form_open("/add") + web.text_input("text", "new task") +
                web.submit_button("add") + web.form_close()
            web.reply_page("{name}", body)
        } elif req["path"] == "/add" and req["method"] == "POST" {
            let text = web.form_value(str(req["body"]), "text")
            if len(text) > 0 {
                items.push(text)
            }
            web.redirect_to("/")
        } else {
            web.not_found("page")
        }
    }
}
'''

_TRAIN = '''import "std/ml.psd" as ml
import "std/data.psd" as data

# {name} — train a small model from CSV (last column = label)
# Run: psd run main.psd

fn main() {
    write_file("/tmp/{name}.csv", "f1,f2,label\\n0,0,0\\n0,1,0\\n1,0,0\\n2,2,1\\n3,2,1\\n2,3,1\\n")
    let loaded = data.load_numbers("/tmp/{name}.csv", true)
    if loaded["ok"] {
        let rows = data.to_matrix(loaded["value"])
        let xs = data.features(rows, 2)
        let ys = data.column(rows, 2)
        let model = ml.fit_logistic(xs, ys, 0.1, 1500)
        if model["ok"] {
            print("accuracy:", ml.accuracy(model, xs, ys))
            print("predict [0,0]:", ml.predict_label(model, [0.0, 0.0]))
            ml.save("/tmp/{name}_model.json", model)
        }
    }
}
'''

_AGENT = '''import "std/agent.psd" as agent

# {name} — give a model tools (offline demo with a simulated reply)
# Run: psd run main.psd
# Real model: resp = json_parse(str(agent.chat(url, env("API_KEY"), payload)["body"]))

fn run_tool(name: str, args: json) -> str {
    if name == "time_now" {
        return str(time_ms())
    }
    return "error: unknown tool " + name
}

fn main() {
    let tools: [json] = []
    tools.push(agent.tool_spec("time_now", "current time in ms", agent.string_params([])))
    let msgs: [json] = []
    msgs.push(agent.system_msg("Use tools when needed."))
    msgs.push(agent.user_msg("What time is it?"))
    let payload = agent.chat_payload("demo-model", msgs, tools)
    print("tools offered:", len(payload["tools"]))
    let resp = json_parse("{\\"choices\\": [{\\"message\\": {\\"tool_calls\\": [{\\"id\\": \\"c1\\", \\"function\\": {\\"name\\": \\"time_now\\", \\"arguments\\": \\"{}\\"}}]}}]}")
    if agent.wants_tools(resp) {
        let calls = agent.tool_calls(resp)
        for i in range(len(calls)) {
            let result = run_tool(str(calls[i]["name"]), calls[i]["args"])
            print(str(agent.tool_result(str(calls[i]["id"]), result)["content"]))
        }
    }
}
'''

TEMPLATES: dict[str, tuple[str, str]] = {
    # name -> (main.psd source, one-line description)
    "cli": (_CLI, "CLI tool: args + files + try_* error handling"),
    "api": (_API, "API client: http_request + json payloads"),
    "web": (_WEB, "web GUI: std/web components, form + redirect flow"),
    "train": (_TRAIN, "ML: CSV -> logistic classifier -> saved model"),
    "agent": (_AGENT, "LLM tool-calling loop (runs offline with a mock reply)"),
}


def render(template: str, name: str) -> str:
    return TEMPLATES[template][0].replace("{name}", name)
