import { Mic, Square } from 'lucide-react';
import './VoiceButton.css';

interface VoiceButtonProps {
  isRecording: boolean;
  isProcessing: boolean;
  onStart: () => void;
  onStop: () => void;
}

export default function VoiceButton({ isRecording, isProcessing, onStart, onStop }: VoiceButtonProps) {
  if (isProcessing) {
    return (
      <button type="button" className="voice-btn processing" disabled>
        <Mic size={18} style={{ opacity: 0.5 }} />
      </button>
    );
  }

  if (isRecording) {
    return (
      <button type="button" className="voice-btn recording" onClick={onStop}>
        <Square size={20} />
        <div className="pulse-ring"></div>
      </button>
    );
  }

  return (
    <button type="button" className="voice-btn idle" onClick={() => {
      console.log("VoiceButton clicked");
      onStart();
    }}>
      <Mic size={20} />
    </button>
  );
}
