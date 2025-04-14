from flask import Flask, render_template, request, redirect, session, url_for
import pandas as pd
import random
import re
import json
from datetime import datetime
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# -------------------------------
# CSV Loading and Setup
# -------------------------------
# Adjust the file path if needed
df = pd.read_excel(r"C:\Users\svelo\OneDrive\Desktop\aptitude_project\aptitude_question_difficulty_refined.xlsx")
categories = df['category'].unique().tolist()

# -------------------------------
# LLM Initialization
# -------------------------------
API = "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"
llm_question_generator = ChatGroq(temperature=1.3, groq_api_key=API, model_name="llama3-70b-8192")
llm_answer_solver = ChatGroq(temperature=0.7, groq_api_key=API, model_name="llama3-70b-8192")

# -------------------------------
# Data Classes
# -------------------------------
class Question:
    def __init__(self, id, question, options, answer, explanation, category, difficulty):
        self.id = id
        self.question = question
        self.options = options
        self.answer = answer
        self.explanation = explanation
        self.category = category
        self.difficulty = difficulty

class User:
    def __init__(self):
        self.rating = 800.0
        self.score = 0.0
        self.attempts = []  # List of tuples: (question_id, outcome, question_difficulty)

    def record_attempt(self, question_id, outcome, question_difficulty):
        self.attempts.append((question_id, outcome, question_difficulty))
        if outcome == 1:
            self.score += question_difficulty

# -------------------------------
# LLM-based Question Generation
# -------------------------------
prompt_generate = PromptTemplate.from_template(
    """
    You are an aptitude question generator. Given a question, correct answer, and explanation,
    generate a NEW question that has the same underlying logic and category but uses different numbers or objects.

    Ensure it uses a similar formula and structure. Then solve the question and provide:
    - 4 options (including the correct one)
    - Correct answer
    - Explanation

    Respond ONLY in this JSON format:
    {{
        "question": "...",
        "options": ["...", "...", "...", "..."],
        "answer": "...",
        "explanation": "..."
    }}

    Original Question: {question}
    Answer: {answer}
    Explanation: {explanation}
    """
)

def generate_similar_question(given_question, given_answer, given_explanation, max_retries=2):
    chain = prompt_generate | llm_question_generator
    for _ in range(max_retries + 1):
        try:
            response = chain.invoke({
                "question": given_question,
                "answer": given_answer,
                "explanation": given_explanation
            }).content
            response_clean = re.sub(r'^.*?({)', r'\1', response, flags=re.DOTALL)
            data = json.loads(response_clean)
            if all(k in data for k in ("question", "options", "answer", "explanation")) and \
               isinstance(data["options"], list) and len(data["options"]) == 4:
                return data
        except Exception as e:
            continue
    # Fallback if API generation fails
    return {
        "question": given_question,
        "options": ["Option A", "Option B", "Option C", "Option D"],
        "answer": given_answer,
        "explanation": given_explanation
    }

# -------------------------------
# Routes
# -------------------------------
@app.route('/', methods=['GET'])
def index():
    return render_template('index.html', categories=categories)

@app.route('/start_test', methods=['POST'])
def start_test():
    selected = request.form.getlist('categories')
    if len(selected) != 3:
        return "Please select exactly 3 categories."
    
    # Filter questions based on selected categories and sample up to 50 questions
    pool = df[df['category'].isin(selected)]
    qbank = [
        Question(row['s.no'], row['questions'], row['options'], row['answer'],
                 row['explanation'], row['category'], row['difficulty'])
        for _, row in pool.iterrows()
    ]
    sample_count = min(50, len(qbank))
    questions = random.sample(qbank, sample_count)
    # Save question bank and user data in the session (convert objects to dict)
    session['questions'] = [q.__dict__ for q in questions]
    session['user'] = User().__dict__
    session['index'] = 0
    session['phase'] = 'Calibration'
    return redirect(url_for('question'))

@app.route('/question', methods=['GET'])

def question():
    index = session.get('index', 0)
    questions = session.get('questions', [])
    total_questions = len(questions)
    if index >= total_questions:
        return redirect(url_for('results'))
    
    # Set phase: Calibration for first 15 questions, Adaptive thereafter
    if index < 15:
        session['phase'] = 'Calibration'
    else:
        session['phase'] = 'Adaptive'
    
    # Get the current question from the session and generate a similar variant
    current_q = questions[index]
    qgen = generate_similar_question(current_q['question'], current_q['answer'], current_q['explanation'])
    # Update current question text and options in session
    session['questions'][index]['question'] = qgen['question']
    session['questions'][index]['options'] = qgen['options']
    # Store the correct answer and explanation for feedback
    session['current_correct'] = qgen['answer']
    session['current_explanation'] = qgen['explanation']
    
    question_data = {
        'q': qgen['question'],
        'options': qgen['options']
    }
    # Pass current question number and total questions
    return render_template(
        'question.html',
        question=question_data,
        phase=session['phase'],
        current_question_number=index+1,
        total_questions=total_questions
    )


@app.route('/submit_answer', methods=['POST'])

def submit_answer():
    selected_option = request.form.get('option')
    index = session.get('index', 0)
    questions = session.get('questions', [])
    current_q = questions[index]
    
    # Prepare user object from session
    user_data = session.get('user')
    user = User()
    user.__dict__.update(user_data)
    
    # Retrieve correct answer and explanation from session
    correct_answer = session.get('current_correct', '')
    explanation = session.get('current_explanation', '')
    
    # Defensive check: ensure both selected_option and correct_answer are strings before comparing
    if selected_option is not None and correct_answer is not None:
        if selected_option.strip() == str(correct_answer).strip():
            outcome = 1
        else:
            outcome = 0
    else:
        outcome = 0

    # Calculate rating adjustment
    expected = 1.0 / (1 + 10 ** ((current_q['difficulty'] - user.rating) / 400))
    old_rating = user.rating
    user.rating += 20 * (outcome - expected)
    user.record_attempt(current_q['id'], outcome, current_q['difficulty'])
    
    # Save updated user info and increment the question index
    session['user'] = user.__dict__
    session['index'] = index + 1
    
    # Store the feedback details in session for display on the feedback page
    session['feedback'] = {
        'selected_option': selected_option,
        'correct_answer': correct_answer,
        'explanation': explanation,
        'outcome': outcome,
        'old_rating': round(old_rating, 2),
        'new_rating': round(user.rating, 2)
    }
    return redirect(url_for('feedback'))

@app.route('/feedback', methods=['GET'])
def feedback():
    feedback = session.get('feedback', {})
    # Determine if there are more questions left
    index = session.get('index', 0)
    total = len(session.get('questions', []))
    next_available = index < total
    return render_template('feedback.html', feedback=feedback, next_available=next_available)

@app.route('/results', methods=['GET'])
def results():
    user_data = session.get('user')
    user = User()
    user.__dict__.update(user_data)
    return render_template('results.html', rating=round(user.rating, 2), score=round(user.score, 2))

# -------------------------------
# Run the Flask App
# -------------------------------
if __name__ == '__main__':
    app.run(debug=True)
