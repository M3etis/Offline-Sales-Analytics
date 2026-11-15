import { useState, useRef, useCallback, useEffect } from 'react';
import toast from 'react-hot-toast';
import { voiceApi } from '../shared/api/voice.api';

export function useVoice() {
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const abortControllerRef = useRef<AbortController | null>(null);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentTextRef = useRef<string | null>(null);
  const [audioState, setAudioState] = useState<{ text: string | null, status: 'loading' | 'playing' | 'paused' | 'stopped' }>({ text: null, status: 'stopped' });

  const startRecording = useCallback(async () => {
    console.log("startRecording function called!");
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        toast.error("Запись звука недоступна: нужен доступ к микрофону по HTTPS или localhost");
        return;
      }
      setIsProcessing(true);
      
      const streamPromise = navigator.mediaDevices.getUserMedia({ audio: true });
      const timeoutPromise = new Promise<MediaStream>((_, reject) => 
        setTimeout(() => reject(new Error("Timeout")), 10000)
      );
      
      let stream: MediaStream;
      try {
        stream = await Promise.race([streamPromise, timeoutPromise]);
      } catch (raceErr) {
        // Разрешение могли дать уже после таймаута — такой поток иначе держал бы микрофон включённым
        streamPromise.then((late) => late.getTracks().forEach((t) => t.stop())).catch(() => {});
        throw raceErr;
      }
      
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
        toast.error("Микрофон не ответил. Разрешите доступ: Системные настройки → Конфиденциальность и безопасность → Микрофон", { duration: 8000 });
      } else {
        toast.error("Нет доступа к микрофону. Проверьте разрешения браузера");
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
            toast.error("Речь не распознана. Говорите громче или проверьте микрофон");
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

      const url = voiceApi.synthesizeStreamUrl(cleanedText, localStorage.getItem('token'));
      const audio = new Audio(url);
      currentAudioRef.current = audio;
      // Синтез идёт несколько секунд до первого звука — в это время озвучку тоже можно остановить
      setAudioState({ text, status: 'loading' });

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
      audio.onerror = () => {
        // Синтез не удался — иначе сообщение осталось бы в состоянии «загружается»
        if (currentAudioRef.current === audio) {
          currentAudioRef.current = null;
          setAudioState({ text: null, status: 'stopped' });
        }
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
      // Уход со страницы во время записи: остановить запись и освободить микрофон —
      // иначе индикатор микрофона горел до перезагрузки
      const recorder = mediaRecorderRef.current;
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        if (recorder.state !== 'inactive') recorder.stop();
        recorder.stream.getTracks().forEach((t) => t.stop());
        mediaRecorderRef.current = null;
      }
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
