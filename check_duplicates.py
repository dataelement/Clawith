#!/usr/bin/env python3
import json

def find_duplicates(data, path=''):
    seen = set()
    duplicates = []
    
    if isinstance(data, dict):
        for key, value in data.items():
            new_path = f"{path}.{key}" if path else key
            if isinstance(value, dict):
                duplicates.extend(find_duplicates(value, new_path))
            elif isinstance(value, str) and value in seen:
                duplicates.append(new_path)
            else:
                seen.add(value)
    
    return duplicates

def main():
    zh_file = '/opt/Clawith/frontend/src/i18n/zh.json'
    
    try:
        with open(zh_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        duplicates = find_duplicates(data)
        
        if duplicates:
            print("重复的翻译条目：")
            for item in duplicates:
                print(f"- {item}")
            print(f"\n总计 {len(duplicates)} 个重复条目")
        else:
            print("没有发现重复的翻译条目")
            
    except Exception as e:
        print(f"错误：{e}")

if __name__ == "__main__":
    main()