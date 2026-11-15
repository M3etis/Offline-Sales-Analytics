import { Mic, Square } from 'lucide-react';
import { IconButton, Spinner } from '../shared/ui';
import './VoiceButton.css';

interface VoiceButtonProps {
  isRecording: boolean;
  isProcessing: boolean;
  onStart: () => void;
  onStop: () => void;
}

/** Микрофон: спокойный в покое, красная точка записи — это живое состояние */
export default function VoiceButton({ isRecording, isProcessing, onStart, onStop }: VoiceButtonProps) {
  if (isProcessing) {
    return <IconButton className="voice-btn is-processing" label="Распознаю речь" icon={<Spinner size={14} />} disabled />;
  }

  if (isRecording) {
    return (
      <IconButton
        className="voice-btn is-recording"
        label="Остановить запись"
        icon={
          <>
            <span className="voice-btn__dot" aria-hidden="true" />
            <Square />
          </>
        }
        onClick={onStop}
      />
    );
  }

  return <IconButton className="voice-btn" label="Задать вопрос голосом" icon={<Mic />} onClick={onStart} />;
}
