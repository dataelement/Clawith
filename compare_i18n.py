#!/usr/bin/env python3
import json
import sys

def load_json(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def find_missing_keys(en_data, zh_data, path=''):
    missing = []
    
    if isinstance(en_data, dict):
        for key, value in en_data.items():
            new_path = f"{path}.{key}" if path else key
            if key not in zh_data:
                missing.append(new_path)
            else:
                missing.extend(find_missing_keys(value, zh_data[key], new_path))
    
    return missing

def main():
    en_file = '/opt/Clawith/frontend/src/i18n/en.json'
    zh_file = '/opt/Clawith/frontend/src/i18n/zh.json'
    
    try:
        en_data = load_json(en_file)
        zh_data = load_json(zh_file)
        
        missing_keys = find_missing_keys(en_data, zh_data)
        
        if missing_keys:
            print("中文国际化文件中缺失的条目：")
            for key in missing_keys:
                print(f"- {key}")
            print(f"\n总计缺失 {len(missing_keys)} 个条目")
        else:
            print("中文国际化文件完整，没有缺失条目")
            
    except Exception as e:
        print(f"错误：{e}")
        sys.exit(1)

if __name__ == "__main__":
    main()