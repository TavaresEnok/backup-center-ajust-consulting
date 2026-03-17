import os
import re

for root, _, files in os.walk("app/templates"):
    for file in files:
        if file.endswith(".html"):
            path = os.path.join(root, file)
            with open(path, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    classes = re.findall(r"class=[\"']([^\"']+)[\"']", line)
                    for cls_str in classes:
                        words = cls_str.split()
                        
                        has_issue = False
                        issue_words = []
                        
                        # Look for bg-gray-900 or bg-gray-800 or bg-slate-900 or text-white
                        for word in words:
                            if word in ["bg-gray-900", "bg-gray-800", "bg-slate-900", "bg-slate-800"]:
                                if f"dark:{word}" not in words and word not in line:
                                    has_issue = True
                                    issue_words.append(word)
                                elif not ("dark:" in word):
                                    has_issue = True
                                    issue_words.append(word)
                                    
                            if word == "text-white":
                                if not any(color in cls_str for color in ["bg-brand", "bg-blue", "bg-error", "bg-success", "bg-purple", "bg-green", "bg-red", "bg-indigo", "dark:text-white"]):
                                    has_issue = True
                                    issue_words.append(word)
                                    
                        if has_issue:
                            print(f"{path}:{i+1}: {cls_str} (Issues: {issue_words})")
