# The Cloud Functions for Firebase SDK to create Cloud Functions and set up triggers.
from firebase_functions import firestore_fn, https_fn

# The Firebase Admin SDK to access Cloud Firestore.
from firebase_admin import initialize_app, firestore
import google.cloud.firestore
import os
import requests
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
import re, json
from datetime import date, timedelta

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


app = initialize_app()

@https_fn.on_request()
def helloWorld(req: https_fn.Request) -> https_fn.Response:
    return https_fn.Response("Hello world!")

@https_fn.on_request(timeout_sec=500)
def chat(request: https_fn.Request) -> https_fn.Response:
    request_json = request.get_json(silent=True)
    print(request_json)

    if request_json is None:
        return https_fn.Response(status=400, body="Invalid JSON data")

    # Access data from the JSON body to get user chat question
    user_question = request_json.get('user_question')

    user_intent_extract = extract_user_intent(user_question)

    user_response = "I do not know the answer, sorry"

    if user_intent_extract is None:
        user_response = handle_default_intent(user_question)
    elif user_intent_extract["intent"] == "get_economic_calendar":
        user_response = handle_get_economic_calendar(user_intent_extract)
    elif user_intent_extract["intent"] == "get_history":
        user_response = handle_get_history(user_intent_extract)
    elif user_intent_extract["intent"] == "get_event_details":
        user_response = handle_get_event_details(user_intent_extract)
    else:
        user_response = handle_default_intent(user_question)

    return https_fn.Response(user_response)

def extract_user_intent(user_question):
    """
    Classify user question into different intents and map to corresponding actions needed
    If the intent requires API calls, then extract API call arguments from user questions as well
    """
    
    # TODO: Tune this so that it is more robust against different ways of asking the same question
    prompt = """You are a smart customer question intent classifier. You are given a customer question and should clasify into
either the following intents:

1. "get_economic_calendar": user is asking the economic calendar or todays' or upcoming macro economic events. Optionally user would ask for a particular country's events calendar
2. "get_history": user is asking the history of an event, user should provide the name of the event and the country
3. "get_event_details": user is asking to explain the details or definition of an event, user should provide the name of the event
4. "other": user is asking for something else

User question:
{}

Return your answer in the following xml format. If user questions does not contain any of the arguments, then do not return that along with xml tags:

<intent>
Intent of user, should be one of the get_economic_calendar, get_history, or other
</intent>

<event_name>
Optional event name
</event_name>

<country_code>
Optional country code, if user is asking event of a particular country, you should convert the country into the corresponding code
For example USA shoul be US, Canada should be CA, etc
</country_code>

User question: {}
"""

    final_prompt = prompt.format(user_question, user_question)

    response = client.chat.completions.create(model="gpt-4o",
    messages=[{
        "role": "user",
        "content": final_prompt
    }
    ])
    response_text = response.choices[0].message.content

    user_question_extracts = extract_xml_to_json(response_text, ["intent", "event_name", "country_code"])
    if user_question_extracts["intent"] is None:
        return None

    return user_question_extracts

    
def extract_xml_to_json(text, tags):
    data = {}
    for tag in tags:
        match = re.search(f'<{tag}>(.*?)</{tag}>', text, re.DOTALL)
        if match:
            data[tag] = match.group(1).strip()
    return data

def handle_get_economic_calendar(user_intent_extract):
    country_code = None
    if "country_code" in user_intent_extract:
        country_code = user_intent_extract["country_code"]
    # get start_date, end_date between today and 1 week after today in yyyy-mm-dd format
    start_date = date.today().strftime("%Y-%m-%d")
    end_date = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")

    url = 'https://geteventsindaterange-rozzd6eg5q-uc.a.run.app/getEventsInDateRange'
    params = {
        'startDate': start_date,
        'endDate': end_date,
        'country': 'US'
    }
    if country_code is not None:
        params['country'] = country_code
    print(params)
    response = requests.get(url, params=params)
    print("Got response: ", response)
    response_json = response.json()[:10]
    print(response_json)

    prompt = """You are an informative financial analyst trying to answer what macro economic events are there in the upcoming weeks.
The context to support your response is provided below in json format.
You should clearly list out all the events along with their contexts for the user.
If the response is not valid, respond: 'Sorry, I do not have relevant information'.
Please answer using professional and concise tone.

Context: {}

"""

    final_prompt = prompt.format(json.dumps(response_json))
    return generate_llm_response(final_prompt)

def handle_get_history(user_intent_extract):
    # 在这个情况中我们需要根据用户的提问，返回相关的历史数据，用户的提问会给一个event的名字，我们使用event_name来call Get history for event来拿到过去90天的历史数据，给用户信息
    # 用户问题例子 What is the historical value of "10-Year NTN-F Auction"?
    # 用户问题例子 What is the historical value of "CPI"?
    url = "https://gethistoryforevent-rozzd6eg5q-uc.a.run.app/getHistoryForEvent"
    event_name = user_intent_extract.get("event_name")
    country_code = user_intent_extract.get("country_code", 'US')
    start_date = (date.today() - timedelta(days=90)).strftime("%Y-%m-%d")
    end_date = date.today().strftime("%Y-%m-%d")
    params = {
        'country': country_code,
        'event': event_name,
        'startDate': start_date,
        'endDate': end_date,
    }
    print(params)
    response = requests.get(url, params=params)
    print("Got response: ", response)
    response_json = response.json()
    print(response_json)

    prompt = """You are an informative financial analyst trying to answer the historical value of a economic event / indicator / data.
    The context to support your response is provided below in json format.
    You should clearly list out all the events along with their contexts for the user.
    If the response is not valid, respond: 'Sorry, I do not have relevant information'.
    Please answer using professional and concise tone.

    Context: {}

    """

    final_prompt = prompt.format(json.dumps(response_json))
    return generate_llm_response(final_prompt)

def handle_get_event_details(user_intent_extract):
    # 在这个情况中我们需要根据用户的提问，来返回一些economic事件的定义，我们不用API，我们直接让LLM生成
    # 示例问题：What is the definition of 10-Year NTN-F Auction
    # 示例问题：What is the definition of CPI
    prompt = """You are an informative financial analyst trying to answer the definition of a economic event or financial products.
        The context to support your response is provided below in json format.
        You should clearly list out all the details along with their contexts for the user.
        If the response is not valid, respond: 'Sorry, I do not have relevant information'.
        Please answer using professional and concise tone.

        Context: {}

        """

    final_prompt = prompt.format(json.dumps(user_intent_extract))
    return generate_llm_response(final_prompt)

def handle_default_intent(user_question):
    prompt = """You are an informative financial analyst trying to answer the user's question.
            The context to support your response is provided below in json format.
            Please answer using professional and concise tone.

            Context: {}

            """
    final_prompt = prompt.format(json.dumps(user_question))
    return generate_llm_response(final_prompt)

def generate_llm_response(prompt):
    global client
    response = client.chat.completions.create(model="gpt-4o",
    messages=[{
        "role": "user",
        "content": prompt
    }
    ])
    return response.choices[0].message.content


if __name__ == '__main__':
    # user_question = "What is the economic calendar for the upcoming week?"
    # user_intent_extract = extract_user_intent(user_question)
    # if user_intent_extract["intent"] == "get_economic_calendar":
    #     user_response = handle_get_economic_calendar(user_intent_extract)
    #     print(https_fn.Response(user_response))
    user_question = "What is the historical value of 10-Year NTN-F Auction?"
    user_intent_extract = extract_user_intent(user_question)
    if user_intent_extract["intent"] == "get_event_details":
        user_response = handle_get_event_details(user_intent_extract)
        print(https_fn.Response(user_response))
    # user_question = "What is the historical value of 10-Year Note Auction"
    # user_intent_extract = extract_user_intent(user_question)
    # if user_intent_extract["intent"] == "get_history":
    #     user_response = handle_get_history(user_intent_extract)
    #     print(https_fn.Response(user_response))
    # user_question = "what's the correct way to calculate the return? Give me an example"
    # user_intent_extract = extract_user_intent(user_question)
    # if user_intent_extract["intent"] not in ["get_economic_calendar", "get_history", "get_event_details"]:
    #     user_response = handle_default_intent(user_question)
    #     print(https_fn.Response(user_response))
