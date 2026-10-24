#!/usr/bin/env python3
"""Compare generated client configs without displaying scalar values.

Paths use JSON Pointer escaping (~0 for ~, ~1 for /). Array order matters.
This checks parsed structure; it does not validate kernel configuration schemas.
"""
import argparse
import json
from pathlib import Path
import sys


def read_config(path):
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    elif path.suffix.lower() in (".yaml", ".yml"):
        import yaml
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            raise ValueError("invalid YAML") from None
    else:
        raise ValueError("unsupported file extension")
    if not isinstance(data, dict):
        raise ValueError("configuration must be an object")
    return data


def changes(before, after, path=""):
    if type(before) is not type(after):
        return [{"path": path, "type": "change"}]
    result = []
    if isinstance(before, dict):
        keys = before.keys() | after.keys()
        if not all(isinstance(key, str) for key in keys):
            raise ValueError("configuration keys must be strings")
        for key in sorted(keys):
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before:
                result.append({"path": child, "type": "add"})
            elif key not in after:
                result.append({"path": child, "type": "remove"})
            else:
                result.extend(changes(before[key], after[key], child))
    elif isinstance(before, list):
        for index in range(max(len(before), len(after))):
            child = path + "/" + str(index)
            if index >= len(before):
                result.append({"path": child, "type": "add"})
            elif index >= len(after):
                result.append({"path": child, "type": "remove"})
            else:
                result.extend(changes(before[index], after[index], child))
    elif before != after:
        result.append({"path": path, "type": "change"})
    return result


def main():
    parser = argparse.ArgumentParser(description="比较已生成 JSON/YAML；仅输出差异路径，不输出值。")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    try:
        result = changes(read_config(args.before), read_config(args.after))
    except ImportError:
        print("缺少 PyYAML，无法读取 YAML；请使用已有 PyYAML 的测试环境。", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, RecursionError):
        print("无法比较：请检查文件可读、扩展名为 JSON/YAML 且内容为有效配置对象。", file=sys.stderr)
        return 2
    print(json.dumps({"count": len(result), "changes": result}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
