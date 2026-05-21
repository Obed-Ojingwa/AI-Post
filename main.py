import os
import asyncio
import logging
import random
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# WORDPRESS PUBLISHER
# =========================================================

load_dotenv()

WP_USERNAME = os.getenv("WORDPRESS_USERNAME")
WP_PASSWORD = os.getenv("WORDPRESS_PASSWORD")
WP_SITE = os.getenv("WORDPRESS_SITE", "").rstrip("/")
WP_API_URL = f"{WP_SITE}/wp-json/wp/v2/posts" if WP_SITE else None
WORDPRESS_CATEGORY_ID = int(os.getenv("WORDPRESS_CATEGORY_ID", "0"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

WORKING_DAYS = os.getenv(
    "WORKING_DAYS",
    "Monday,Tuesday,Wednesday,Thursday,Friday"
).split(",")
POSTS_PER_DAY = int(os.getenv("POSTS_PER_DAY", "2"))
MIN_AI_DELAY = int(os.getenv("MIN_AI_DELAY", "8"))
MAX_AI_DELAY = int(os.getenv("MAX_AI_DELAY", "15"))
USE_SCHEDULED_POSTS = os.getenv(
    "USE_SCHEDULED_POSTS",
    "true"
).lower() in ("1", "true", "yes")
POST_START_HOUR = int(os.getenv("POST_START_HOUR", "8"))
POST_END_HOUR = int(os.getenv("POST_END_HOUR", "18"))

PROMPT_TEMPLATE = ""
TOPICS = []

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def load_inputs():
    global PROMPT_TEMPLATE, TOPICS

    if not os.path.exists("prompts.txt"):
        raise FileNotFoundError("prompts.txt file not found.")

    if not os.path.exists("topics.txt"):
        raise FileNotFoundError("topics.txt file not found.")

    with open("prompts.txt", encoding="utf-8") as prompt_file:
        PROMPT_TEMPLATE = prompt_file.read().strip()

    with open("topics.txt", encoding="utf-8") as topics_file:
        TOPICS = [
            line.strip()
            for line in topics_file
            if line.strip()
        ]


load_inputs()


def publish_to_wordpress(title, content, publish_time=None):
    """
    Publish or schedule WordPress posts.

    IMPORTANT:

    Your WordPress REST API endpoint is:

    https://wideodom.eu/wp-json/wp/v2/posts
    """

    if not WP_API_URL:
        raise ValueError("WORDPRESS_SITE is not configured.")

    status = "future" if publish_time else "publish"

    payload = {
        "title": title,
        "content": content,
        "status": status,
        "categories": [WORDPRESS_CATEGORY_ID]
    }

    if publish_time:
        payload["date"] = publish_time.isoformat()

    response = requests.post(
        WP_API_URL,
        auth=(WP_USERNAME, WP_PASSWORD),
        json=payload,
        timeout=120
    )

    if response.status_code in [200, 201]:
        logging.info(f"Published: {title}")
        print(f"SUCCESS: {title}")
    else:
        logging.error(
            "WordPress publish failed: %s", response.text
        )
        print(response.text)


async def generate_openai_response(provider_name, prompt):
    if not OPENAI_API_KEY:
        return (
            f"[{provider_name}] Placeholder response. "
            "No OPENAI_API_KEY configured."
        )

    system_message = (
        f"You are {provider_name}. Reply in Polish as a marketing copywriter "
        "for a local Warsaw service company."
    )
    request_payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.75,
        "max_tokens": 1500
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    response = await asyncio.to_thread(
        requests.post,
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=request_payload,
        timeout=120
    )

    if response.status_code != 200:
        raise Exception(
            f"{provider_name} request failed: {response.status_code} {response.text}"
        )

    result = response.json()
    return result["choices"][0]["message"]["content"].strip()


async def generate_chatgpt(playwright, prompt):
    return await generate_openai_response("ChatGPT", prompt)


async def generate_gemini(playwright, prompt):
    return await generate_openai_response("Gemini", prompt)


async def generate_grok(playwright, prompt):
    return await generate_openai_response("Grok", prompt)


async def process_topic(playwright, topic, publish_time=None):
    prompt = PROMPT_TEMPLATE.replace("{topic}", topic)

    logging.info(f"Generating content for: {topic}")

    chatgpt_task = generate_chatgpt(playwright, prompt)
    await asyncio.sleep(random.randint(MIN_AI_DELAY, MAX_AI_DELAY))

    gemini_task = generate_gemini(playwright, prompt)
    await asyncio.sleep(random.randint(MIN_AI_DELAY, MAX_AI_DELAY))

    grok_task = generate_grok(playwright, prompt)

    responses = await asyncio.gather(
        chatgpt_task,
        gemini_task,
        grok_task,
        return_exceptions=True
    )

    cleaned_responses = []

    for response in responses:
        if isinstance(response, Exception):
            logging.error(f"Generation exception: {str(response)}")
            continue
        if response and len(response.strip()) > 100:
            cleaned_responses.append(response)

    if not cleaned_responses:
        logging.error(
            f"No valid responses generated for topic: {topic}"
        )
        return

    article = build_article(
        topic=topic,
        responses=cleaned_responses
    )

    publish_to_wordpress(
        title=topic,
        content=article,
        publish_time=publish_time
    )

    cooldown = random.randint(60, 180)
    logging.info(
        f"Cooldown after publishing: {cooldown} seconds"
    )
    await asyncio.sleep(cooldown)


def build_article(topic, responses):
    generated_sections = []
    for index, response in enumerate(responses, start=1):
        generated_sections.append(
            f"<h2>Odpowiedź AI {index}</h2>\n<p>{response}</p>"
        )

    return (
        f"<h1>{topic}</h1>\n"
        + "\n".join(generated_sections)
    )


def generate_post_times(posts_per_day):
    now = datetime.now()
    start_time = now.replace(
        hour=POST_START_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )
    if start_time < now:
        start_time = now + timedelta(minutes=1)

    end_time = now.replace(
        hour=POST_END_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )
    if end_time <= start_time:
        end_time += timedelta(days=1)

    if posts_per_day < 1:
        return []

    total_seconds = (end_time - start_time).total_seconds()
    if total_seconds <= 0:
        return []

    interval_seconds = total_seconds / posts_per_day
    schedule = []
    for index in range(posts_per_day):
        offset = interval_seconds * index
        jitter = random.uniform(0, min(600, interval_seconds / 2))
        publish_time = start_time + timedelta(seconds=offset + jitter)
        if publish_time > end_time:
            publish_time = end_time
        schedule.append(publish_time)

    schedule.sort()
    return schedule


# =========================================================
# MAIN ENTRY
# =========================================================

async def main():
    today = datetime.now().strftime("%A")

    if today not in WORKING_DAYS:
        print(f"{today} is not a configured working day.")
        return

    logging.info("Starting automation system")

    schedule_times = generate_post_times(POSTS_PER_DAY)
    if not schedule_times:
        logging.error(
            "No schedule times generated. "
            "Increase time window or reduce posts/day."
        )
        return

    async with async_playwright() as playwright:
        for index, topic in enumerate(TOPICS):
            if index >= POSTS_PER_DAY:
                break
            if index >= len(schedule_times):
                break

            publish_time = schedule_times[index]
            now = datetime.now()
            wait_seconds = (publish_time - now).total_seconds()

            if wait_seconds > 0:
                print(
                    f"\nWaiting for scheduled post time..."
                    f"\nTopic: {topic}"
                    f"\nPublish Time: {publish_time}"
                    f"\nSleeping for {int(wait_seconds)} seconds\n"
                )
                await asyncio.sleep(wait_seconds)

            try:
                logging.info(f"Processing topic {index + 1}: {topic}")
                await process_topic(
                    playwright=playwright,
                    topic=topic,
                    publish_time=(
                        publish_time if USE_SCHEDULED_POSTS else None
                    )
                )
            except Exception as e:
                logging.error(
                    f"Processing failed for topic {topic}: {str(e)}"
                )
                failure_cooldown = random.randint(180, 600)
                print(f"Failure cooldown: {failure_cooldown} seconds")
                await asyncio.sleep(failure_cooldown)

    logging.info("Automation completed")


# =========================================================
# SAFE STARTUP CHECKS
# =========================================================

def validate_environment():
    required = [WP_USERNAME, WP_PASSWORD, WP_SITE]
    if not all(required):
        raise Exception(
            "\nMissing environment variables.\n"
            "Check your .env file.\n"
            "Required:\n"
            "- WORDPRESS_USERNAME\n"
            "- WORDPRESS_PASSWORD\n"
            "- WORDPRESS_SITE\n"
        )

    if not os.path.exists("topics.txt"):
        raise Exception("topics.txt file not found.")

    if not os.path.exists("prompts.txt"):
        raise Exception("prompts.txt file not found.")

    os.makedirs("profiles/chatgpt", exist_ok=True)
    os.makedirs("profiles/gemini", exist_ok=True)
    os.makedirs("profiles/grok", exist_ok=True)

    logging.info("Environment validation successful")


# =========================================================
# APPLICATION START
# =========================================================

if __name__ == "__main__":
    try:
        validate_environment()
        print(
            "\n===================================="
            "\nAI WORDPRESS AUTOMATION STARTING"
            "\n====================================\n"
        )
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAutomation stopped manually.")
    except Exception as e:
        logging.error(str(e))
        print(f"\nStartup failed:\n{str(e)}")