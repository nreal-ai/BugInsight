#!/usr/bin/env python3
"""Delete a Feishu Project comment via Direct API."""
import urllib.request
import json
import sys
import os

PLUGIN_ID = os.environ.get("FEISHU_PLUGIN_ID", "")
PLUGIN_SECRET = os.environ.get("FEISHU_PLUGIN_SECRET", "")
USER_KEY = os.environ.get("FEISHU_USER_KEY", "")


def get_token():
    data = json.dumps({
        "plugin_id": PLUGIN_ID,
        "plugin_secret": PLUGIN_SECRET,
        "type": 0,
    }).encode()
    req = urllib.request.Request(
        "https://project.feishu.cn/open_api/authen/plugin_token",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["data"]["token"]


def delete_comment(project_key, work_item_type, work_item_id, comment_id):
    token = get_token()
    url = (
        f"https://project.feishu.cn/open_api/{project_key}"
        f"/work_item/{work_item_type}/{work_item_id}/comment/{comment_id}"
    )
    req = urllib.request.Request(url, method="DELETE")
    req.add_header("x-plugin-token", token)
    req.add_header("x-user-key", USER_KEY)
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        if result.get("err_code") == 0:
            print(f"OK {comment_id}")
            return True
        else:
            print(f"ERR {comment_id}: {result.get('err_msg')}", file=sys.stderr)
            return False


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("Usage: delete_comment.py <project_key> <type> <work_item_id> <comment_id>")
        sys.exit(1)
    ok = delete_comment(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    sys.exit(0 if ok else 1)
