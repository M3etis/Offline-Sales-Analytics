import { useState, useRef, useCallback, useEffect } from 'react';
import { voiceApi } from '../services/api';

export function useVoice() {
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const abortControllerRef = useRef<AbortController | null>(null);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentTextRef = useRef<string | null>(null);
  const [audioState, setAudioState] = useState<{ text: string | null, status: 'playing' | 'paused' | 'stopped' }>({ text: null, status: 'stopped' });

  const startRecording = useCallback(async () => {
    console.log("startRecording function called!");
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert("Ваш браузер не поддерживает запись аудио или требуется HTTPS соединение (или localhost).");
        return;
      }
      setIsProcessing(true);
      
      const streamPromise = navigator.mediaDevices.getUserMedia({ audio: true });
      const timeoutPromise = new Promise<MediaStream>((_, reject) => 
        setTimeout(() => reject(new Error("Timeout")), 10000)
      );
      
      const stream = await Promise.race([streamPromise, timeoutPromise]);
      
      setIsProcessing(false);
      
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (err: any) {
      setIsProcessing(false);
      console.error("Error accessing microphone", err);
      if (err.message === "Timeout") {
        alert("Браузер не отвечает на запрос микрофона. Пожалуйста, проверьте настройки конфиденциальности macOS (Системные настройки -> Конфиденциальность и безопасность -> Микрофон) и убедитесь, что вашему браузеру разрешен доступ.");
      } else {
        alert("Не удалось получить доступ к микрофону. Проверьте разрешения в браузере.");
      }
    }
  }, []);

  const stopRecording = useCallback((): Promise<string> => {
    return new Promise((resolve, reject) => {
      if (!mediaRecorderRef.current) {
        reject("No recorder");
        return;
      }

      mediaRecorderRef.current.onstop = async () => {
        setIsRecording(false);
        setIsProcessing(true);
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        const controller = new AbortController();
        abortControllerRef.current = controller;

        try {
          const res = await voiceApi.transcribe(blob, controller.signal);
          console.log("Transcribed text:", res.text);
          if (!res.text) {
            alert("Не удалось распознать речь. Возможно, микрофон работает некорректно или звук был слишком тихим.");
          }
          resolve(res.text);
        } catch (err: any) {
          if (err.name === 'AbortError') {
            resolve('');
          } else {
            reject(err);
          }
        } finally {
          setIsProcessing(false);
          abortControllerRef.current = null;
          // Clean up tracks
          mediaRecorderRef.current?.stream.getTracks().forEach(track => track.stop());
        }
      };

      mediaRecorderRef.current.stop();
    });
  }, []);

  const playSynthesizedAudio = useCallback((text: string) => {
    try {
      // If same text is already playing or paused, resume it
      if (currentAudioRef.current && currentTextRef.current === text) {
        if (currentAudioRef.current.paused && !currentAudioRef.current.ended) {
          console.log("🔊 Resuming existing audio for same text");
          currentAudioRef.current.play().catch(e => console.error("Audio resume error:", e));
          return;
        }
        // If already playing the same text, don't create duplicate
        if (!currentAudioRef.current.paused) {
          console.log("🔊 Audio already playing for this text, skipping");
          return;
        }
      }

      // Stop any currently playing audio before starting new one
      if (currentAudioRef.current) {
        console.log("⏹️ Stopping previous audio before playing new one");
        currentAudioRef.current.pause();
        currentAudioRef.current.currentTime = 0;
        currentAudioRef.current = null;
      }

      currentTextRef.current = text;
      console.log("🎵 Starting new audio synthesis for text:", text.substring(0, 50) + "...");

      // Remove text in parentheses (like exact numbers) before sending to TTS
      const cleanedText = text.replace(/\s*\([^)]*\)/g, '');

      const token = localStorage.getItem('token');
      const tokenParam = token ? `&token=${encodeURIComponent(token)}` : '';
      const url = `/api/v1/voice/synthesize_stream?text=${encodeURIComponent(cleanedText)}${tokenParam}`;
      const audio = new Audio(url);
      currentAudioRef.current = audio;

      // Start playing immediately, browser handles the stream
      audio.play().catch(e => console.error("Audio play error:", e));

      audio.onplay = () => {
        console.log("▶️ Audio started playing");
        setAudioState({ text, status: 'playing' });
      };
      audio.onpause = () => {
        console.log("⏸️ Audio paused");
        setAudioState(prev => prev.text === text ? { text, status: 'paused' } : prev);
      };
      audio.onended = () => {
        console.log("✅ Audio playback ended");
        setAudioState({ text: null, status: 'stopped' });
        if (currentAudioRef.current === audio) {
          currentAudioRef.current = null;
        }
      };
    } catch (err) {
      console.error("Failed to play audio", err);
    }
  }, []);

  const stopAudio = useCallback(() => {
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
      setAudioState({ text: null, status: 'stopped' });
    }
  }, []);

  const pauseAudio = useCallback(() => {
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
    }
  }, []);

  const resumeAudio = useCallback(() => {
    if (currentAudioRef.current) {
      currentAudioRef.current.play().catch(e => console.error("Audio resume error:", e));
    }
  }, []);

  useEffect(() => {
    return () => {
      stopAudio();
    };
  }, [stopAudio]);

  return {
    isRecording,
    isProcessing,
    startRecording,
    stopRecording,
    playSynthesizedAudio,
    stopAudio,
    pauseAudio,
    resumeAudio,
    audioState
  };
}
