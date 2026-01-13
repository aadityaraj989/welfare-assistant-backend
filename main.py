import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import json
import httpx
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import openai

# Load environment variables
try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")
    print("Using environment variables or defaults...")

# Email configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL")

# Initialize clients
openai_api_key = os.getenv("OPENAI_API_KEY")
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
relay_webhook_url = os.getenv("RELAY_WEBHOOK_URL", "https://httpbin.org/post")  # Changed to demo URL since we're using direct email

if not openai_api_key:
    print("Warning: No OpenAI API key found, using demo mode")
else:
    openai.api_key = openai_api_key

app = FastAPI(title="Welfare Scheme Assistant", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Alternative dev port
        "https://welfare-assistant-frontend.vercel.app",  # Vercel deployment (update with your actual URL)
        "*"  # Allow all origins for now (restrict in production)
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class ChatRequest(BaseModel):
    message: str
    session_id: str

class ChatResponse(BaseModel):
    reply: str

# Session state model
class UserSession(BaseModel):
    session_id: str
    name: Optional[str] = None
    age: Optional[int] = None
    income: Optional[float] = None
    state: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    eligible_schemes: Optional[list] = None
    current_field: str = "name"  # Track which field we're collecting
    conversation_history: list = []

# In-memory session storage (in production, use Redis or database)
sessions = {}

def send_eligibility_email(user_data):
    """Send eligibility email directly using SMTP"""
    try:
        # Create message with proper headers to avoid "Show quoted text"
        msg = MIMEMultipart('alternative')
        msg['From'] = FROM_EMAIL
        msg['To'] = user_data['email']
        msg['Subject'] = "Your Government Welfare Scheme Eligibility Results"
        msg['X-Mailer'] = 'Government Welfare Assistant'
        msg['Reply-To'] = FROM_EMAIL

        # Email body - clean, simple text without extra whitespace
        schemes_text = "\n".join(f"• {scheme}" for scheme in user_data['eligible_schemes'])

        text_body = f"""Dear {user_data['name']},

Thank you for using our Government Welfare Scheme Assistant!

Based on your information:
- Age: {user_data['age']} years
- Annual Income: ₹{user_data['income']}
- State: {user_data['state']}
- Phone: {user_data['phone']}

You may be eligible for the following government welfare schemes:
{schemes_text}

Please visit your nearest government office or the respective scheme website for detailed information and application procedures.

For more information about specific schemes, you can:
1. Visit the official government websites
2. Contact your local government offices
3. Use our chat assistant for more details

Best regards,
Government Welfare Scheme Assistant"""

        # Create HTML version to ensure proper formatting
        html_body = f"""<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
<h2 style="color: #2c5aa0;">Your Government Welfare Scheme Eligibility Results</h2>

<p>Dear <strong>{user_data['name']}</strong>,</p>

<p>Thank you for using our Government Welfare Scheme Assistant!</p>

<h3>Your Information:</h3>
<ul>
<li><strong>Age:</strong> {user_data['age']} years</li>
<li><strong>Annual Income:</strong> ₹{user_data['income']}</li>
<li><strong>State:</strong> {user_data['state']}</li>
<li><strong>Phone:</strong> {user_data['phone']}</li>
</ul>

<h3>You may be eligible for the following government welfare schemes:</h3>
<ul>
"""

        for scheme in user_data['eligible_schemes']:
            html_body += f"<li>{scheme}</li>"

        html_body += """
</ul>

<p>Please visit your nearest government office or the respective scheme website for detailed information and application procedures.</p>

<h3>For more information about specific schemes, you can:</h3>
<ol>
<li>Visit the official government websites</li>
<li>Contact your local government offices</li>
<li>Use our chat assistant for more details</li>
</ol>

<p>Best regards,<br>
<strong>Government Welfare Scheme Assistant</strong></p>
</body>
</html>"""

        # Attach both plain text and HTML versions
        msg.attach(MIMEText(text_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))

        # Send email
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        text = msg.as_string()
        server.sendmail(FROM_EMAIL, user_data['email'], text)
        server.quit()

        print(f"[SUCCESS] Email sent to {user_data['email']}")
        return True

    except Exception as e:
        print(f"[FAILED] Email sending failed: {e}")
        return False

# Helper functions
def get_session(session_id: str) -> UserSession:
    if session_id not in sessions:
        sessions[session_id] = UserSession(session_id=session_id)
    return sessions[session_id]

def validate_age(age_str: str) -> Optional[int]:
    try:
        age = int(age_str)
        if 0 < age < 120:
            return age
    except ValueError:
        pass
    return None

def validate_income(income_str: str) -> Optional[float]:
    try:
        income = float(income_str.replace(',', '').replace('₹', '').replace('$', ''))
        if income >= 0:
            return income
    except ValueError:
        pass
    return None

def validate_phone(phone_str: str) -> Optional[str]:
    """Validate phone number (10 digits, with optional country code)"""
    import re
    phone_str = phone_str.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')

    # Remove country code if present (+91, 91, etc.)
    if phone_str.startswith('+91'):
        phone_str = phone_str[3:]
    elif phone_str.startswith('91') and len(phone_str) > 10:
        phone_str = phone_str[2:]

    # Check if it's exactly 10 digits
    if re.match(r'^\d{10}$', phone_str):
        return phone_str
    return None

def validate_email(email_str: str) -> Optional[str]:
    """Validate email address"""
    import re
    email_str = email_str.strip().lower()

    # Basic email regex pattern
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

    if re.match(email_pattern, email_str):
        return email_str
    return None

def evaluate_eligibility(session: UserSession) -> dict:
    """Use OpenAI to evaluate eligibility based on user profile"""
    prompt = f"""
    Based on the following user profile, determine eligibility for Indian government welfare schemes:

    Name: {session.name}
    Age: {session.age}
    Annual Income: ₹{session.income}
    State: {session.state}

    Consider major Indian government welfare schemes like:
    - Ayushman Bharat ( Pradhan Mantri Jan Arogya Yojana) - Health insurance for low-income families
    - PM Awas Yojana - Housing scheme for low-income families
    - MGNREGA - Rural employment guarantee
    - PDS (Public Distribution System) - Food subsidies
    - PM Kisan - Agricultural subsidies
    - Ujjwala Yojana - LPG connections for BPL families
    - Swachh Bharat Mission - Sanitation benefits
    - Sukanya Samriddhi Yojana - Girl child savings scheme
    - Atal Pension Yojana - Pension scheme for unorganized sector

    Also consider state-specific schemes based on the state: {session.state}

    Return a JSON response with:
    {{
        "eligible_schemes": ["scheme_name_1", "scheme_name_2", ...],
        "reasoning": "brief explanation of eligibility"
    }}

    Focus on schemes where the user likely qualifies based on age, income, and location.
    """

    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500
        )
        content = response.choices[0].message.content

        # Extract JSON from response
        start_idx = content.find('{')
        end_idx = content.rfind('}') + 1
        if start_idx != -1 and end_idx > start_idx:
            json_str = content[start_idx:end_idx]
            result = json.loads(json_str)
            return result
        else:
            return {"eligible_schemes": [], "reasoning": "Unable to determine eligibility"}
    except Exception as e:
        print(f"Error evaluating eligibility: {e}")
        # Fallback: simulate eligibility based on basic criteria
        eligible_schemes = []
        if session.age and session.age < 60 and session.income and session.income < 500000:
            eligible_schemes = ["Ayushman Bharat", "PM Awas Yojana"]
        elif session.age and session.age >= 60:
            eligible_schemes = ["Atal Pension Yojana", "PM Kisan"]

        return {
            "eligible_schemes": eligible_schemes,
            "reasoning": "Eligibility determined using fallback logic due to API issues"
        }

def save_to_supabase(session: UserSession):
    """Save user eligibility data to Supabase"""
    try:
        data = {
            "session_id": session.session_id,
            "name": session.name,
            "age": session.age,
            "income": session.income,
            "state": session.state,
            "phone": session.phone,
            "email": session.email,
            "eligible_schemes": session.eligible_schemes or [],
            "created_at": datetime.utcnow().isoformat()
        }

        print("=== SUPABASE SAVE OPERATION ===")
        print("Saving the following record to database:")
        print(json.dumps(data, indent=2))

        # Try to save to real Supabase if credentials are available and not demo
        if supabase_url and supabase_key and supabase_url != "https://demo.supabase.co":
            try:
                headers = {
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json"
                }
                import requests as req
                response = req.post(
                    f"{supabase_url}/rest/v1/user_eligibility",
                    json=data,
                    headers=headers
                )
                print(f"[SUCCESS] Real Supabase save: {response.status_code}")
            except Exception as real_error:
                print(f"[FAILED] Real Supabase save failed: {real_error}")
        else:
            print("[DEMO] Data logged (set real SUPABASE_URL/SUPABASE_KEY for actual saving)")
        print("=================================")

    except Exception as e:
        print(f"Error in database save: {e}")

def trigger_webhook(session: UserSession):
    """Send eligibility email to user"""
    print("DEBUG: trigger_webhook function called")
    try:
        user_data = {
            "name": session.name,
            "age": session.age,
            "income": session.income,
            "state": session.state,
            "phone": session.phone,
            "email": session.email,
            "eligible_schemes": session.eligible_schemes or []
        }

        print("=== EMAIL NOTIFICATION ===")
        print("Sending eligibility email to user:")
        print(f"Name: {user_data['name']}")
        print(f"Email: {user_data['email']}")
        print(f"Eligible Schemes: {len(user_data['eligible_schemes'])} schemes")

        # Send email directly
        if send_eligibility_email(user_data):
            print("[SUCCESS] Eligibility email sent successfully")
        else:
            print("[FAILED] Email sending failed - check SMTP configuration")

        print("===================================")

    except Exception as e:
        print(f"Error in email notification: {e}")

def process_message(session: UserSession, user_message: str) -> str:
    """Process user message and return response"""
    user_message = user_message.lower().strip()

    # Add user message to conversation history
    session.conversation_history.append({"role": "user", "message": user_message})

    # Determine which field to collect next
    if not session.name:
        # Extract name from message
        if len(user_message.split()) >= 1 and user_message not in ['hi', 'hello', 'hey', 'start']:
            session.name = user_message.title()
            session.current_field = "age"
            response = "What is your age?"
        else:
            response = "Hello! I'm here to help you find government welfare schemes you may be eligible for. What is your name?"

    elif not session.age:
        age = validate_age(user_message)
        if age:
            session.age = age
            session.current_field = "income"
            response = "What is your annual income (in rupees)?"
        else:
            response = "Please provide a valid age (numbers only, between 1-120)."

    elif not session.income:
        income = validate_income(user_message)
        if income:
            session.income = income
            session.current_field = "state"
            response = "Which state do you live in?"
        else:
            response = "Please provide a valid annual income (numbers only, e.g., 120000)."

    elif not session.state:
        if len(user_message) > 2:  # Basic validation
            session.state = user_message.title()
            session.current_field = "phone"
            response = "What is your phone number?"
        else:
            response = "Please provide a valid state name."

    elif not session.phone:
        phone = validate_phone(user_message)
        if phone:
            session.phone = phone
            session.current_field = "email"
            response = "What is your email address?"
        else:
            response = "Please provide a valid phone number (10 digits, e.g., 9876543210)."

    elif not session.email:
        email = validate_email(user_message)
        if email:
            session.email = email
            session.current_field = "complete"

            # All data collected, evaluate eligibility
            result = evaluate_eligibility(session)
            session.eligible_schemes = result.get("eligible_schemes", [])

            schemes_text = "\n".join(f"• {scheme}" for scheme in session.eligible_schemes) if session.eligible_schemes else "No specific schemes identified"

            response = f"Based on your details, you may be eligible for:\n{schemes_text}\n\nYou will receive a detailed email with your eligibility results."

            # Save to database and send email
            save_to_supabase(session)
            trigger_webhook(session)
        else:
            response = "Please provide a valid email address (e.g., user@example.com)."

    else:
        # Handle follow-up questions
        if "tell me more" in user_message or "more about" in user_message:
            response = "I can provide more details about specific schemes. Please mention which scheme you'd like to know more about."
        else:
            response = "I've already assessed your eligibility. Check your email for detailed results, or start over with different information?"

    # Add response to conversation history
    session.conversation_history.append({"role": "assistant", "message": response})

    return response

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        session = get_session(request.session_id)
        response = process_message(session, request.message)
        return ChatResponse(reply=response)

    except Exception as e:
        print(f"Error processing chat: {e}")
        return ChatResponse(reply="Sorry, I encountered an error. Please try again.")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)