import { useState, useRef, useEffect, ImgHTMLAttributes } from 'react';
import { createPortal } from 'react-dom';
import { Send, Bot, User, ThermometerSun, Maximize2, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './Chatbot.css';

interface Message {
  id: string;
  role: 'user' | 'agent';
  text: string;
}

const ImageWithModal = (props: ImgHTMLAttributes<HTMLImageElement>) => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <div className="image-wrapper">
        <img {...props} className="chat-image-thumbnail" onClick={() => setIsOpen(true)} alt={props.alt || "Chart visualization"} />
        <button className="expand-btn" onClick={() => setIsOpen(true)} title="View Full Size">
          <Maximize2 size={16} />
        </button>
      </div>

      {isOpen && createPortal(
        <div className="image-modal-overlay" onClick={() => setIsOpen(false)}>
          <button className="close-modal-btn" onClick={() => setIsOpen(false)}>
            <X size={24} />
          </button>
          <img {...props} className="chat-image-full" onClick={(e) => e.stopPropagation()} alt={props.alt || "Expanded chart"} />
        </div>,
        document.body
      )}
    </>
  );
};

export default function Chatbot() {
  const [messages, setMessages] = useState<Message[]>([{
    id: '1',
    role: 'agent',
    text: `Hello! I am the **UTCI Heat Tracker Agent**. I analyze spatial and temporal thermal comfort and heat stress trends across Kerala's districts and taluks, and can generate custom data visualizations on demand.

My conclusions are based on the last 7 days of meteorological observations acquired from the **ECMWF** (European Centre for Medium-Range Weather Forecasts) model. The daily data readings are acquired for two specific timestamps: **1:30 PM** (time of usual peak of heat stress) and **10:30 PM** (to understand thermal comfort in the night time, when the human body is supposed to cool down).

### What is UTCI?
The **Universal Thermal Climate Index (UTCI)** is a biometeorological metric (expressed in **°C** or degree Centigrade) that represents the human body's physiological response to the outdoor thermal environment. It takes into account air temperature, humidity, wind speed, and solar/terrestrial radiation.

### UTCI Thermal Comfort Ranges:
*   **Above 32°C**: Strong to Extreme Heat Stress (dangerous)
*   **26°C to 32°C**: Moderate Heat Stress
*   **9°C to 26°C**: No Thermal Stress (Optimal Comfort)
*   **Below 9°C**: Cold Stress

### Try asking me:
1. *Show me the average UTCI value in Palakkad for yesterday afternoon.*
2. *Is there any district experiencing strong heat stress (above 32°C) today?*
3. *Plot a line chart of the daily UTCI trend for Thrissur over the last week.*`
  }]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  useEffect(() => {
    fetch('http://localhost:8000/api/last_update')
      .then(res => res.json())
      .then(data => {
        if (data.last_update) {
          const date = new Date(data.last_update);
          setLastUpdate(date.toLocaleString());
        }
      })
      .catch(console.error);
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMsg: Message = { id: Date.now().toString(), role: 'user', text: input.trim() };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setIsLoading(true);

    const agentMsgId = (Date.now() + 1).toString();
    setMessages(prev => [...prev, { id: agentMsgId, role: 'agent', text: '' }]);

    try {
      const response = await fetch('http://localhost:8000/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg.text })
      });

      if (!response.ok) throw new Error('Failed to fetch from backend');
      if (!response.body) throw new Error('No response body');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.slice(6);
            if (!dataStr) continue;
            
            try {
              const event = JSON.parse(dataStr);
              let newText = '';
              
              if (event.content && event.content.parts) {
                for (const part of event.content.parts) {
                  if (part.text) newText += part.text;
                }
              } else if (event.text) {
                newText += event.text;
              }

              if (newText) {
                setMessages(prev => prev.map(msg => 
                  msg.id === agentMsgId ? { ...msg, text: msg.text + newText } : msg
                ));
              }
            } catch(e) {
              console.error('Error parsing SSE JSON:', e);
            }
          }
        }
      }
    } catch (error) {
      console.error(error);
      setMessages(prev => prev.map(msg => 
        msg.id === agentMsgId ? { ...msg, text: "Sorry, I encountered an error communicating with the backend." } : msg
      ));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="chat-container">
      <div className="chat-header">
        <ThermometerSun className="header-icon" size={28} />
        <div>
          <h1>UTCI Heat Tracker</h1>
          <p>Kerala Thermal Comfort Intelligence {lastUpdate && `• Last Updated: ${lastUpdate}`}</p>
        </div>
      </div>
      
      <div className="chat-messages">
        {messages.map((msg) => (
          <div key={msg.id} className={`message ${msg.role}`}>
            <div className="avatar">
              {msg.role === 'agent' ? <Bot size={20} /> : <User size={20} color="white" />}
            </div>
            <div className="message-content">
              {msg.role === 'agent' ? (
                <ReactMarkdown 
                  remarkPlugins={[remarkGfm]}
                  components={{ img: (props) => <ImageWithModal {...props} /> }}
                >
                  {msg.text}
                </ReactMarkdown>
              ) : (
                msg.text
              )}
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="message agent">
            <div className="avatar"><Bot size={20} /></div>
            <div className="message-content">
              <div className="typing-indicator">
                <span></span><span></span><span></span>
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form className="chat-input-area" onSubmit={handleSubmit}>
        <div className="input-wrapper">
          <input
            type="text"
            className="chat-input"
            placeholder="Ask about UTCI trends, districts, or request charts..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={isLoading}
          />
          <button type="submit" className="send-button" disabled={!input.trim() || isLoading}>
            <Send size={18} />
          </button>
        </div>
      </form>
    </div>
  );
}
