import os
import pandas as pd
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
import re

# =============================
#  Load Excel responses
# =============================
FILE_PATH = os.path.join(os.path.dirname(__file__), "RuriChatbox_Responses.xlsx")
FAQ_RESPONSES = {}

try:
    df = pd.read_excel(FILE_PATH)
    question_col = df.columns[0]
    answer_col = df.columns[1]
    FAQ_RESPONSES = {
        str(row[question_col]).strip().lower(): str(row[answer_col]).strip()
        for _, row in df.iterrows()
        if pd.notna(row[question_col]) and pd.notna(row[answer_col])
    }
    print(f"✅ Excel loaded: {len(FAQ_RESPONSES)} rows")
except Exception as e:
    print("⚠ Excel load error:", e)

# =============================
#  Load fallback AI model
# =============================
try:
    MODEL_NAME = "microsoft/DialoGPT-small"
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
    print("✅ DialoGPT ready")
except Exception as e:
    tokenizer = model = None
    print("⚠ Could not load fallback model:", e)

# =============================
#  NLP Response Function
# =============================
def nlp_model_respond(user_message, max_length=150):
    user_input = user_message.strip().lower()
    print(f"🟡 User asked: {user_input}")

    # 1️⃣ Help / Support detection
    help_keywords = [
        "help", "support", "problem", "issue", "error", "stuck",
        "agent", "human", "representative", "staff", "i need help", "i need support"
    ]
    if any(k in user_input for k in help_keywords):
        print("✅ Matched: Help / Support")
        return "Don't worry — I’ll connect you to a live agent now."

    # 2️⃣ Unrelated / Emotional topics
    if re.search(r"\b(love|miss|like|hate|sad|angry)\b", user_input):
        print("✅ Matched: Unrelated/Emotional")
        return "I didn’t quite understand that — let me connect you to a live agent for better assistance."

    # 3️⃣ Greeting detection
    greetings = ["hi", "hello", "hey", "good morning", "good afternoon"]
    if any(word == user_input for word in greetings):
        print("✅ Matched: Greeting")
        return "Hello! How can I help you today?"

    # 4️⃣ Exact match in Excel
    if user_input in FAQ_RESPONSES:
        print("✅ Matched: Exact FAQ")
        return FAQ_RESPONSES[user_input]

    # 5️⃣ Partial match from Excel
    for q, a in FAQ_RESPONSES.items():
        if q in user_input:
            print("✅ Matched: Partial FAQ")
            return a

    # 6️⃣ Fallback to AI model
    if tokenizer and model:
        inputs = tokenizer.encode(user_message + tokenizer.eos_token, return_tensors="pt")
        outputs = model.generate(inputs, max_length=max_length, pad_token_id=tokenizer.eos_token_id)
        reply = tokenizer.decode(outputs[0], skip_special_tokens=True)
        print("✅ AI fallback used")
        return reply.strip()

    # 7️⃣ Default fallback (if all else fails)
    print("⚠ Default fallback")
    return "I'm not sure about that. Would you like me to connect you to a live agent?"
