# app/services/openai_service.py
from openai import OpenAI
import os
import json
from typing import Dict, Any
from fastapi import HTTPException

class OpenAIService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        self.client = OpenAI(api_key=api_key)
        
    def add_element(self, session_data: Dict[str, Any], description: str) -> Dict[str, Any]:
        """Add a new element based on the description"""
        prompt = self._create_add_element_prompt(session_data, description)
        result = self._process_openai_response(prompt)
        
        print(result)
        # Update session data with new elements
        return {
            "status": "ok",
            "updated_json": {"elements": result["elements"]},
            "answer": result.get("answer", "")
        }
    
    def edit_element(self, session_data: Dict[str, Any], instruction: str) -> Dict[str, Any]:
        """Edit an existing element based on the instruction"""
        prompt = self._create_edit_element_prompt(session_data, instruction)
        result = self._process_openai_response(prompt)
        
        return {
            "status": "ok",
            "updated_json": {"elements": result["elements"]},
            "answer": result.get("answer", "")
        }
    
    def remove_element(self, session_data: Dict[str, Any], instruction: str) -> Dict[str, Any]:
        """Remove an element based on the instruction"""
        prompt = self._create_remove_element_prompt(session_data, instruction)
        result = self._process_openai_response(prompt)
        
        return {
            "status": "ok",
            "updated_json": {"elements": result["elements"]},
            "answer": result.get("answer", "")
        }
    
    def _process_openai_response(self, prompt: str) -> Dict[str, Any]:
        """Process OpenAI API call and handle errors"""
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.1
            )
            return json.loads(f"{response.choices[0].message.content}")
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to parse OpenAI response as JSON: {str(e)}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"OpenAI API Error: {str(e)}"
            )

    def _create_add_element_prompt(self, session_data: Dict[str, Any], description: str) -> str:
        """Create prompt for adding a new element"""
        return f"""
ACHTUNG: Füge NICHT eigenmächtig Oberflächenbefestigung hinzu,
außer der Benutzer schreibt ausdrücklich "Oberflächenbefestigung",
"Gehwegplatten", "Mosaikpflaster", "Verbundpflaster" o.Ä.

Wir arbeiten mit diesem JSON-Schema, wobei "material" + "offset" bei Oberflächenbefestigung Pflicht sind:
{{
  "elements": [
    {{
      "type": "string",
      "length": 0.0,
      "width": 0.0,
      "depth": 0.0,
      "diameter": 0.0,
      "material": "",
      "offset": 0.0
    }}
  ],
  "answer": ""
}}

Aktuelles JSON:
{json.dumps(session_data, indent=2)}

ACHTUNG:
- Alle Maßeinheiten sind in Metern.
- Falls in der Beschreibung z.B. "DN150" vorkommt, dann bedeutet das diameter=0.15 (also DN-Wert / 1000).

1) Füge exakt EIN neues Element hinzu basierend auf: "{description}"
   - Falls "Baugraben" => type="Baugraben" + length,width,depth
   - Falls "Rohr" oder "Druckrohr" => type="Rohr" + length, diameter
   - Falls "Oberflächenbefestigung" o.ä. => type="Oberflächenbefestigung",
       *zusätzlich* material="...", offset=... für Randzone, 
       NICHT depth oder diameter benutzen

2) Schreibe zusätzlich ein kurzes "answer"-Feld (1-2 Sätze),
   z.B. als kleine Zusammenfassung dessen, was du generiert hast.

Gib nur das neue komplette JSON zurück. Es soll so aussehen:
{{
  "elements": [...],
  "answer": "Irgendein kurzer Text..."
}}
"""

    def _create_edit_element_prompt(self, session_data: Dict[str, Any], instruction: str) -> str:
        """Create prompt for editing an existing element"""
        return f"""
Wir haben dieses JSON:
{json.dumps(session_data, indent=2)}

Aufgabe:
1) Suche das Element, das zu "{instruction}" passt.
2) Ändere NUR die relevanten Felder (length, width, depth, diameter, offset, material, …).
3) Lösche KEIN weiteres Element (außer es wurde ausdrücklich gewünscht).
4) Gib das fertige JSON zurück (elements + answer).
   answer=1-2 Sätze, was du geändert hast.

Beachte:
- Alle Maßeinheiten sind in Metern
- Bei DN-Werten (z.B. DN150): diameter = DN-Wert / 1000
- Bei Oberflächenbefestigung sind material + offset Pflicht

Das zurückgegebene JSON muss diesem Schema folgen:
{{
  "elements": [...],
  "answer": "Kurze Beschreibung der Änderung"
}}
"""

    def _create_remove_element_prompt(self, session_data: Dict[str, Any], instruction: str) -> str:
        """Create prompt for removing an element"""
        return f"""
Aktuelles JSON:
{json.dumps(session_data, indent=2)}

1) Finde EIN Element, das zu "{instruction}" passt.
2) Lösche es aus dem JSON.
3) Gib das komplette JSON zurück plus "answer"-Feld:
   "answer": "Was du gelöscht hast."

Das zurückgegebene JSON muss diesem Schema folgen:
{{
  "elements": [...],
  "answer": "Element XYZ wurde gelöscht"
}}
"""

# Error classes for better error handling
class OpenAIServiceError(Exception):
    """Base class for OpenAI service errors"""
    pass

class PromptError(OpenAIServiceError):
    """Error in prompt generation or validation"""
    pass

class ResponseError(OpenAIServiceError):
    """Error in processing OpenAI response"""
    pass
