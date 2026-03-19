import json
import os
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.widgets import TextArea, Button, Label
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.styles import Style
from orchestrator.spec_processor import process_spec
from verification.spec_validation import validate_spec_against_code

# Load schema
schema_path = os.path.join(os.path.dirname(__file__), '../specs/product_schema.json')
with open(schema_path, 'r') as f:
    schema = json.load(f)

# Default spec
default_spec = {
    "title": "New Project",
    "features": [],
    "acceptance_criteria": []
}

def load_spec(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return default_spec

def save_spec(spec, file_path):
    with open(file_path, 'w') as f:
        json.dump(spec, f, indent=2)

def create_editor(spec_path):
    spec = load_spec(spec_path)
    
    # Create text areas for each field
    title_buffer = Buffer(text=spec.get('title', ''))
    features_buffer = Buffer(text='\n'.join(spec.get('features', [])))
    acceptance_buffer = Buffer(text='\n'.join(spec.get('acceptance_criteria', [])))
    
    title_area = TextArea(buffer=title_buffer, height=1, prompt='Title: ')
    features_area = TextArea(buffer=features_buffer, height=10, prompt='Features: ')
    acceptance_area = TextArea(buffer=acceptance_buffer, height=10, prompt='Acceptance Criteria: ')
    
    # Save button
    def save_action(event):
        new_spec = {
            "title": title_buffer.text,
            "features": [f.strip() for f in features_buffer.text.splitlines() if f.strip()],
            "acceptance_criteria": [a.strip() for a in acceptance_buffer.text.splitlines() if a.strip()]
        }
        try:
            process_spec(new_spec, {})
            save_spec(new_spec, spec_path)
            event.app.exit()
        except Exception as e:
            error_label = Label(f"Validation error: {str(e)}")
            layout.children.append(error_label)
    
    save_button = Button(text="Save", handler=save_action)
    cancel_button = Button(text="Cancel", handler=lambda e: e.app.exit())
    
    # Layout
    buttons = VSplit([save_button, cancel_button])
    layout = Layout(
        HSplit([
            title_area,
            features_area,
            acceptance_area,
            buttons
        ])
    )
    
    # Style
    style = Style.from_dict({
        'window': '#ffffff bg:#000000',
        'button': '#ffffff bg:#333333',
        'button focused': '#000000 bg:#ffffff',
        'text-area': '#ffffff bg:#000000',
    })
    
    app = Application(layout=layout, key_bindings=KeyBindings(), style=style, full_screen=True)
    app.run()

if __name__ == "__main__":
    spec_path = os.path.join(os.path.dirname(__file__), '../specs/product_spec.json')
    create_editor(spec_path)