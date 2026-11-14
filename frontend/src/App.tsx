import React, { useState, useEffect, useRef } from 'react';

interface ReasoningStep {
  step_no: number;
  type: string;
  agent: string;
  title: string;
  detail: string;
  evidence: any[];
}

interface IncidentTicket {
  ticket_id: string;
  domain: string;
  title: string;
  store_or_system: string;
  device_model: string;
  symptom: string;
  root_cause: string;
  resolution: string;
  resolved_in_minutes?: number;
  similarity_score?: number;
}

interface SplunkLog {
  log_id: string;
  timestamp: string;
  domain: string;
  host: string;
  level: string;
  error_code: string;
  message: string;
  relevance_score?: number;
}

interface RecoveryRunbook {
  runbook_id: string;
  domain: string;
  target_system: string;
  title: string;
  steps: string[];
  escalation_criteria: string;
  escalation_team: string;
}

interface ChatMessage {
  id: string;
  sessionId?: string;
  sender: 'user' | 'ai';
  text: string;
  timestamp: string;
  imageData?: string;
  assignedAgent?: string;
  assignedAgentLabel?: string;
  reasoningSteps?: ReasoningStep[];
  tickets?: IncidentTicket[];
  logs?: SplunkLog[];
  runbook?: RecoveryRunbook;
  escalationTeam?: string;
  promptToUser?: string;
  suggestedFollowups?: string[];
  technicalDetails?: string;
}

interface Scenario {
  id: string;
  badge: string;
  agent_id: string;
  agent_name: string;
  title: string;
  prompt: string;
  description: string;
}

const AGENT_AVATARS: Record<string, { icon: string; name: string; shortName: string; color: string; badgeBg: string }> = {
  infra_specialist: {
    icon: '/agents/infra_specialist.jpg',
    name: 'インフラ スペシャリスト (AGENT 01)',
    shortName: 'インフラ専門AI',
    color: 'border-blue-400 ring-blue-100',
    badgeBg: 'bg-blue-50 text-blue-800 border-blue-200',
  },
  order_specialist: {
    icon: '/agents/order_specialist.jpg',
    name: '発注管理 スペシャリスト (AGENT 02)',
    shortName: '発注管理専門AI',
    color: 'border-emerald-400 ring-emerald-100',
    badgeBg: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  },
  network_specialist: {
    icon: '/agents/network_specialist.jpg',
    name: '店舗ネットワーク スペシャリスト (AGENT 03)',
    shortName: '店舗NW専門AI',
    color: 'border-purple-400 ring-purple-100',
    badgeBg: 'bg-purple-50 text-purple-800 border-purple-200',
  },
  pos_specialist: {
    icon: '/agents/pos_specialist.jpg',
    name: 'POS スペシャリスト (AGENT 04)',
    shortName: 'POS専門AI',
    color: 'border-amber-400 ring-amber-100',
    badgeBg: 'bg-amber-50 text-amber-800 border-amber-200',
  },
  aiops_orchestrator: {
    icon: '/agents/aiops_orchestrator.jpg',
    name: '総合受付 AIオーケストレーター',
    shortName: '総合受付AI',
    color: 'border-indigo-400 ring-indigo-100',
    badgeBg: 'bg-indigo-50 text-indigo-800 border-indigo-200',
  },
};

function getAgentAvatar(agentId?: string, label?: string) {
  if (agentId && AGENT_AVATARS[agentId]) {
    return AGENT_AVATARS[agentId];
  }
  if (label) {
    if (label.includes('インフラ') || label.includes('AGENT 01')) return AGENT_AVATARS.infra_specialist;
    if (label.includes('発注') || label.includes('AGENT 02')) return AGENT_AVATARS.order_specialist;
    if (label.includes('ネットワーク') || label.includes('AGENT 03')) return AGENT_AVATARS.network_specialist;
    if (label.includes('POS') || label.includes('AGENT 04')) return AGENT_AVATARS.pos_specialist;
  }
  return AGENT_AVATARS.aiops_orchestrator;
}

// 右側 AI 診断カルテの幅（px）
const KARTE_DEFAULT_WIDTH = 440;
const KARTE_MIN_WIDTH = 320;
const KARTE_MAX_WIDTH = 900;
const CHAT_MIN_WIDTH = 360; // カルテを広げてもチャット欄に残す最小幅
const KARTE_KEY_STEP = 20;
const KARTE_WIDTH_STORAGE_KEY = 'aiops.karteWidth';

interface ParsedAnswer {
  instructions: string[];
  reason: string | null;
  closing: string | null;
  rawText: string | null;
}

function parseAnswerText(text: string): ParsedAnswer {
  if (!text) return { instructions: [], reason: null, closing: null, rawText: '' };

  const hasInstructionTag = text.includes('【対応指示】');
  const hasReasonTag = text.includes('【理由】');

  if (!hasInstructionTag && !hasReasonTag) {
    const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
    const hasNumberedSteps = lines.some((l) => /^[1-9]\.\s*/.test(l));
    if (!hasNumberedSteps) {
      return { instructions: [], reason: null, closing: null, rawText: text };
    }
  }

  const instructions: string[] = [];
  let reason: string | null = null;
  let closing: string | null = null;

  let currentSection: 'before' | 'instructions' | 'reason' | 'closing' = 'before';
  const beforeLines: string[] = [];
  const instructionLines: string[] = [];
  const reasonLines: string[] = [];
  const closingLines: string[] = [];

  const rawLines = text.split('\n');
  for (const rawLine of rawLines) {
    const line = rawLine.trim();
    if (!line) continue;

    if (line.includes('【対応指示】')) {
      currentSection = 'instructions';
      continue;
    } else if (line.includes('【理由】')) {
      currentSection = 'reason';
      continue;
    } else if (
      (currentSection === 'reason' || currentSection === 'instructions') &&
      (line.includes('解決しない場合') || line.includes('エスカレーション') || line.includes('ご不明な点') || line.includes('緊急エスカレーション'))
    ) {
      currentSection = 'closing';
      closingLines.push(line);
      continue;
    }

    if (currentSection === 'instructions') {
      instructionLines.push(line);
    } else if (currentSection === 'reason') {
      reasonLines.push(line);
    } else if (currentSection === 'closing') {
      closingLines.push(line);
    } else {
      beforeLines.push(line);
    }
  }

  const linesToParse = instructionLines.length > 0 ? instructionLines : beforeLines;
  if (linesToParse.some((l) => /^[1-9]\.\s*/.test(l))) {
    let currentStep = '';
    for (const line of linesToParse) {
      if (/^[1-9]\.\s*/.test(line)) {
        if (currentStep) instructions.push(currentStep);
        currentStep = line.replace(/^[1-9]\.\s*/, '');
      } else if (currentStep) {
        currentStep += ' ' + line;
      } else {
        currentStep = line;
      }
    }
    if (currentStep) instructions.push(currentStep);
  } else if (instructionLines.length > 0) {
    instructions.push(...instructionLines);
  }

  if (reasonLines.length > 0) {
    reason = reasonLines.join('\n');
  }

  if (closingLines.length > 0) {
    closing = closingLines.join('\n');
  }

  return {
    instructions,
    reason,
    closing,
    rawText: instructions.length === 0 && !reason ? text : null,
  };
}

function StructuredAnswer({ text }: { text: string }) {
  const parsed = parseAnswerText(text);

  if (parsed.rawText !== null) {
    const paragraphs = parsed.rawText.split('\n\n').filter(Boolean);
    return (
      <div className="space-y-2 text-sm sm:text-base leading-relaxed text-slate-800">
        {paragraphs.map((p, idx) => (
          <p key={idx} className="whitespace-pre-line">{p}</p>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* 1. 対応手順 (ステップカード) */}
      {parsed.instructions.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-bold text-blue-900 tracking-wide flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-blue-600"></span>
            対応手順（すぐ行うこと）
          </div>
          <div className="space-y-2">
            {parsed.instructions.map((step, idx) => (
              <div
                key={idx}
                className="flex items-start gap-3 bg-blue-50/60 hover:bg-blue-50 border border-blue-200/80 rounded-xl p-3 sm:p-3.5 transition-colors shadow-2xs"
              >
                <span className="flex-none flex items-center justify-center w-6 h-6 rounded-full bg-blue-600 text-white text-xs font-bold mt-0.5 shadow-xs">
                  {idx + 1}
                </span>
                <span className="text-sm sm:text-base text-slate-900 leading-relaxed font-semibold">
                  {step}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 2. 理由・診断根拠 (コールアウト) */}
      {parsed.reason && (
        <div className="bg-slate-50 border border-slate-200/90 rounded-xl p-3 text-xs sm:text-sm text-slate-700 leading-relaxed shadow-2xs">
          <div className="font-bold text-slate-800 mb-1 flex items-center gap-1.5">
            <span className="text-sm">💡</span>
            <span>診断根拠・原因:</span>
          </div>
          <div className="whitespace-pre-line pl-4 text-slate-600 font-normal">
            {parsed.reason}
          </div>
        </div>
      )}

      {/* 3. 案内・結び文 */}
      {parsed.closing && (
        <div className="text-xs sm:text-sm text-slate-600 font-medium pt-0.5 flex items-center gap-1.5">
          <span>ℹ️</span>
          <span>{parsed.closing}</span>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState('');
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadingPhase, setLoadingPhase] = useState<number>(0);
  // ルーティング完了後、担当の専門AIへのアサイン確定を 1 秒見せるためのフラグ
  const [isAgentAssigned, setIsAgentAssigned] = useState<boolean>(false);
  // 待機中の演出: 'routing' = 総合受付から専門AIへのルーティング / 'thinking' = 担当済みの専門AIが続きの質問を考え中
  const [loadingMode, setLoadingMode] = useState<'routing' | 'thinking'>('routing');
  const [activeTab, setActiveTab] = useState<'chat' | 'reasoning' | 'knowledge'>('chat');
  const [activeReasoningMessage, setActiveReasoningMessage] = useState<ChatMessage | null>(null);
  const [isKarteOpen, setIsKarteOpen] = useState<boolean>(false); // 標準では右側カルテは非表示
  // 右側カルテの幅（PC 幅のみ・ドラッグで変更し localStorage に保存）
  const [karteWidth, setKarteWidth] = useState<number>(() => {
    const saved = Number(window.localStorage.getItem(KARTE_WIDTH_STORAGE_KEY));
    return Number.isFinite(saved) && saved >= KARTE_MIN_WIDTH ? saved : KARTE_DEFAULT_WIDTH;
  });
  const mainRef = useRef<HTMLElement | null>(null);
  const karteDragRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const clampKarteWidth = (w: number) => {
    const mainWidth = mainRef.current?.clientWidth ?? window.innerWidth;
    const max = Math.max(KARTE_MIN_WIDTH, Math.min(KARTE_MAX_WIDTH, mainWidth - CHAT_MIN_WIDTH));
    return Math.round(Math.min(max, Math.max(KARTE_MIN_WIDTH, w)));
  };

  const updateKarteWidth = (w: number, persist: boolean) => {
    const next = clampKarteWidth(w);
    setKarteWidth(next);
    if (persist) {
      window.localStorage.setItem(KARTE_WIDTH_STORAGE_KEY, String(next));
    }
  };

  const handleKarteResizeStart = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    e.preventDefault();
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch (_) {}
    karteDragRef.current = { startX: e.clientX, startWidth: karteWidth };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };

  const handleKarteResizeMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = karteDragRef.current;
    if (!drag) return;
    // カルテは右側なので、左へドラッグ（clientX 減少）で幅が広がる
    updateKarteWidth(drag.startWidth + (drag.startX - e.clientX), false);
  };

  const handleKarteResizeEnd = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!karteDragRef.current) return;
    karteDragRef.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch (_) {}
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    updateKarteWidth(karteWidth, true);
  };

  const handleKarteResizeKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      updateKarteWidth(karteWidth + KARTE_KEY_STEP, true);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      updateKarteWidth(karteWidth - KARTE_KEY_STEP, true);
    }
  };

  // カルテ表示時・ウィンドウリサイズ時に、保存済みの幅を現在の画面に収まる範囲へ補正する
  useEffect(() => {
    if (!isKarteOpen) return;
    const fit = () => setKarteWidth((w) => clampKarteWidth(w));
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isKarteOpen]);
  const [activeSessionId, setActiveSessionId] = useState<string>('web-session');
  const [callingAgentId, setCallingAgentId] = useState<string | null>(null);

  // 音声ステート
  const [isVoiceActive, setIsVoiceActive] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSessionId, setRecordingSessionId] = useState<string | null>(null);
  const [voiceStatus, setVoiceStatus] = useState<string>('待機中');
  const [isAnalyzingVoice, setIsAnalyzingVoice] = useState(false);
  const [isLiveConnected, setIsLiveConnected] = useState<boolean>(false);
  const [connectedLiveModel, setConnectedLiveModel] = useState<string>('gemini-3.8-live');
  const [liveOutputTranscript, setLiveOutputTranscript] = useState<string>('');
  const [liveReasoningSteps, setLiveReasoningSteps] = useState<ReasoningStep[]>([]);
  const liveSocketRef = useRef<WebSocket | null>(null);
  // Gemini Live ネイティブ音声 (24kHz PCM) の Web Audio 再生管理
  const audioCtxRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef<number>(0);
  const liveSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const liveSpeakRef = useRef<{
    text: string;
    msgId: string;
    gotAudio: boolean;
    done: boolean;
    timer: number | null;
  } | null>(null);
  const finalTranscriptRef = useRef<string>('');
  const isHoldingVoiceRef = useRef<boolean>(false);
  const voiceTargetRef = useRef<{ sessionId: string; candidates: string[] } | null>(null);
  const messagesRef = useRef<ChatMessage[]>(messages);
  const activeSessionIdRef = useRef<string>(activeSessionId);
  const handleSendMessageRef = useRef<any>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // テキスト入力欄へフォーカス＆文言セット
  const focusTextInput = (initialText?: string, targetSessionId?: string) => {
    if (initialText !== undefined && initialText !== '') {
      setInputText(initialText);
    }
    if (targetSessionId) {
      setActiveSessionId(targetSessionId);
    }
    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus();
        inputRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 50);
  };

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  // カメラ・画像ステート
  const [capturedImage, setCapturedImage] = useState<string | null>(null);
  const [isCameraModalOpen, setIsCameraModalOpen] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // カメラ起動
  const startCamera = async () => {
    setIsCameraModalOpen(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      mediaStreamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
    } catch (err) {
      console.warn('Camera access denied or unavailable, fallback to file input:', err);
      stopCamera();
      if (fileInputRef.current) {
        fileInputRef.current.click();
      }
    }
  };

  // カメラ停止
  const stopCamera = () => {
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }
    setIsCameraModalOpen(false);
  };

  // 写真撮影
  const capturePhoto = () => {
    if (!videoRef.current) return;
    const video = videoRef.current;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const ctx = canvas.getContext('2d');
    if (ctx) {
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
      setCapturedImage(dataUrl);
      if (!inputText.trim()) {
        setInputText('【現場写真添付】機器のランプ状態・エラー画面を撮影しました。診断してください。');
      }
    }
    stopCamera();
  };

  // ファイルアップロードハンドラ
  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (event) => {
        if (event.target?.result) {
          setCapturedImage(event.target.result as string);
          if (!inputText.trim()) {
            setInputText('【現場写真添付】機器のランプ状態・エラー画面を撮影しました。診断してください。');
          }
        }
      };
      reader.readAsDataURL(file);
    }
  };

  // ナレッジステート
  const [knowledgeData, setKnowledgeData] = useState<{
    tickets: IncidentTicket[];
    logs: SplunkLog[];
    runbooks: RecoveryRunbook[];
  }>({ tickets: [], logs: [], runbooks: [] });
  const [knowledgeCategory, setKnowledgeCategory] = useState<string>('all');
  const [knowledgeSearch, setKnowledgeSearch] = useState<string>('');

  const chatContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<any>(null);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const [playingMessageId, setPlayingMessageId] = useState<string | null>(null);
  const [loadingVoiceId, setLoadingVoiceId] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/scenarios')
      .then((res) => res.json())
      .then((data) => setScenarios(data))
      .catch((err) => console.error('Failed to load scenarios:', err));

    fetch('/api/knowledge')
      .then((res) => res.json())
      .then((data) => setKnowledgeData(data))
      .catch((err) => console.error('Failed to load knowledge:', err));

    const welcomeMsg: ChatMessage = {
      id: 'welcome',
      sender: 'ai',
      text: 'こんにちは！店舗・システム運用のAIアシスタントです。\n4大スペシャリスト（AGENT 01: インフラ、AGENT 02: 発注管理、AGENT 03: 店舗ネットワーク、AGENT 04: POS）が、各領域の障害・問い合わせを迅速に一次切り分けします。\n画面上部の「ワンクリックでお試し」から各スペシャリストのシナリオを選択するか、テキスト・音声（マイク長押し）で自由にご相談ください。',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      assignedAgent: 'aiops_orchestrator',
      assignedAgentLabel: '総合受付 AIオーケストレーター',
    };
    setMessages([welcomeMsg]);
  }, []);

  useEffect(() => {
    if (messages.length <= 1) return;

    // 最新のユーザー質問を画面最上部に配置（文脈と回答を上から順に閲覧可能にする）
    const lastUserMsg = [...messages].reverse().find((m) => m.sender === 'user');
    const targetId = lastUserMsg ? lastUserMsg.id : messages[messages.length - 1].id;

    const timer = setTimeout(() => {
      const elem = document.getElementById(`message-${targetId}`);
      if (elem) {
        elem.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
      }
    }, 60);

    return () => clearTimeout(timer);
  }, [messages, isLoading]);

  // Gemini Live Flash 3.8 WebSocket の接続管理
  const ensureLiveWebSocket = (targetSession?: string) => {
    const sessId = targetSession || activeSessionIdRef.current || 'default';
    if (liveSocketRef.current && liveSocketRef.current.readyState === WebSocket.OPEN) {
      return liveSocketRef.current;
    }
    try {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      const wsUrl = `${protocol}//${host}/ws/live/user-demo/${sessId}`;
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        setIsLiveConnected(true);
        console.log('⚡ Gemini Live Flash 3.8 WebSocket Connected:', wsUrl);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'connected') {
            setIsLiveConnected(true);
            if (msg.live_model) {
              setConnectedLiveModel(msg.live_model);
            }
          } else if (msg.type === 'reasoning_step' && msg.step) {
            setLiveReasoningSteps((prev) => [...prev, msg.step]);
          } else if (msg.type === 'transcription_output') {
            setLiveOutputTranscript(msg.text);
          } else if (msg.type === 'audio_chunk' && msg.data) {
            playLivePcmChunk(msg.data, msg.mime_type);
          } else if (msg.type === 'turn_complete') {
            handleLiveTurnComplete();
          } else if (msg.type === 'interrupted') {
            stopLivePlayback();
            setPlayingMessageId(null);
          }
        } catch (_) {}
      };

      ws.onclose = () => {
        setIsLiveConnected(false);
        // 読み上げ依頼中に切断され音声未着なら TTS へフォールバック
        if (liveSpeakRef.current && !liveSpeakRef.current.gotAudio) {
          fallbackLiveToTts();
        }
      };

      liveSocketRef.current = ws;
      return ws;
    } catch (e) {
      console.warn('Live WebSocket connection error:', e);
      return null;
    }
  };

  // 音声入力の処理: 更問いの場合はルーティングや候補照合を挟まず、自然なLive会話としてそのまま送信
  const processVoiceInput = async (spokenText: string) => {
    const text = spokenText.trim();
    if (!text) {
      setRecordingSessionId(null);
      voiceTargetRef.current = null;
      return;
    }

    const specificTarget = voiceTargetRef.current;
    const recentMessages = [...messagesRef.current].reverse();
    const lastAiMsg = recentMessages.find(
      (m) => m.sender === 'ai' && m.sessionId
    );
    const targetSession = specificTarget?.sessionId || lastAiMsg?.sessionId || activeSessionIdRef.current;

    setIsAnalyzingVoice(true);
    setVoiceStatus(`⚡ Gemini Live「${text}」で自然対話中...`);

    // 質問文は Live へ送らない（Live が別の回答音声を生成し二重読み上げになるため）。
    // 回答確定後に speakWithLive() で専門AIの回答原稿のみを Live に読み上げ依頼する。

    // 発話テキストそのままで、現在対応中の専門AIと直接Live対話（音声質問フラグ isVoiceOrigin: true）
    if (handleSendMessageRef.current) {
      handleSendMessageRef.current(text, targetSession, null, true);
    }
    setIsAnalyzingVoice(false);
    setRecordingSessionId(null);
    voiceTargetRef.current = null;
  };

  // 音声入力の開始 (Push-to-Talk: ボタン押下時)
  const startVoiceInput = (targetSessionId?: string, targetCandidates?: string[]) => {
    if (isHoldingVoiceRef.current) return;
    isHoldingVoiceRef.current = true;

    if (targetSessionId) {
      voiceTargetRef.current = { sessionId: targetSessionId, candidates: targetCandidates || [] };
      setRecordingSessionId(targetSessionId);
    } else {
      voiceTargetRef.current = null;
      setRecordingSessionId(null);
    }

    // 前の再生音声があれば停止
    stopAudio();
    // ユーザー操作中に AudioContext を準備（ブラウザの自動再生制限対策）
    ensureAudioContext();

    // Gemini Live Flash 3.8 WebSocket のセッションを準備
    ensureLiveWebSocket(targetSessionId);

    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      alert('お使いのブラウザは音声認識に対応していません。テキスト入力をご利用ください。');
      isHoldingVoiceRef.current = false;
      setRecordingSessionId(null);
      return;
    }

    try {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort();
        } catch (_) {}
      }

      const recognition = new SpeechRecognition();
      recognition.lang = 'ja-JP';
      recognition.continuous = true; // 途中で間が空いても途切れないよう連続認識
      recognition.interimResults = true;

      recognition.onstart = () => {
        setIsRecording(true);
        setIsVoiceActive(true);
        finalTranscriptRef.current = '';
        setLiveReasoningSteps([]);
        setLiveOutputTranscript('');
        setVoiceStatus('⚡ Gemini Live Flash 3.8 リアルタイム録音中... 離すと即時送信');
      };

      recognition.onresult = (event: any) => {
        let fullTranscript = '';
        for (let i = 0; i < event.results.length; ++i) {
          fullTranscript += event.results[i][0].transcript;
        }
        finalTranscriptRef.current = fullTranscript;
        setInputText(fullTranscript);
        setVoiceStatus(`⚡ Gemini Live リアルタイム認識: 「${fullTranscript}」`);
      };

      recognition.onerror = (event: any) => {
        console.warn('SpeechRecognition error:', event.error);
        if (event.error !== 'no-speech') {
          setIsRecording(false);
          setIsVoiceActive(false);
          isHoldingVoiceRef.current = false;
          setRecordingSessionId(null);
          setVoiceStatus(`音声認識エラー: ${event.error}`);
        }
      };

      recognition.onend = () => {
        // ボタンを押し続けている間にブラウザ都合で一時切断された場合は再接続
        if (isHoldingVoiceRef.current) {
          try {
            recognition.start();
            return;
          } catch (_) {}
        }
        setIsRecording(false);
        setIsVoiceActive(false);
        const finalText = finalTranscriptRef.current.trim() || inputText.trim();
        finalTranscriptRef.current = '';
        if (finalText) {
          processVoiceInput(finalText);
        } else {
          setRecordingSessionId(null);
          setVoiceStatus('🎙️ ボタンを押し続けながら話してください（離すと送信されます）');
        }
      };

      recognitionRef.current = recognition;
      recognition.start();
    } catch (e) {
      console.error('Speech recognition start failed:', e);
      setIsRecording(false);
      setIsVoiceActive(false);
      isHoldingVoiceRef.current = false;
      setRecordingSessionId(null);
    }
  };

  // 音声入力の終了＆送信 (ボタンから指/マウスを離した時)
  const stopVoiceInputAndSend = () => {
    if (!isHoldingVoiceRef.current && !isRecording) return;
    isHoldingVoiceRef.current = false;
    setVoiceStatus('🎙️ 音声入力を完了中... 送信準備をしています');

    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop();
      } catch (_) {
        setIsRecording(false);
        setIsVoiceActive(false);
      }
    } else {
      setIsRecording(false);
      setIsVoiceActive(false);
    }
  };

  // 音声入力のキャンセル
  const cancelVoiceInput = () => {
    isHoldingVoiceRef.current = false;
    finalTranscriptRef.current = '';
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort();
      } catch (_) {}
    }
    setIsRecording(false);
    setIsVoiceActive(false);
    setVoiceStatus('音声入力をキャンセルしました');
  };

  // Web Audio 用 AudioContext を準備（ユーザー操作中に呼ぶと自動再生制限を回避できる）
  const ensureAudioContext = (): AudioContext | null => {
    try {
      if (!audioCtxRef.current) {
        const Ctx = window.AudioContext || (window as any).webkitAudioContext;
        if (!Ctx) return null;
        audioCtxRef.current = new Ctx();
      }
      if (audioCtxRef.current.state === 'suspended') {
        audioCtxRef.current.resume().catch(() => {});
      }
      return audioCtxRef.current;
    } catch (_) {
      return null;
    }
  };

  // Live 音声の再生を即時停止し、読み上げ依頼状態を破棄
  const stopLivePlayback = () => {
    liveSourcesRef.current.forEach((src) => {
      try {
        src.onended = null;
        src.stop();
      } catch (_) {}
    });
    liveSourcesRef.current = [];
    nextPlayTimeRef.current = 0;
    const st = liveSpeakRef.current;
    if (st?.timer) clearTimeout(st.timer);
    liveSpeakRef.current = null;
  };

  // 読み上げ依頼の最初の音声到着時に、ロード表示から再生中表示へ切り替える
  const markSpeakStarted = (st: NonNullable<typeof liveSpeakRef.current>) => {
    if (st.gotAudio) return;
    st.gotAudio = true;
    if (st.timer) {
      clearTimeout(st.timer);
      st.timer = null;
    }
    setLoadingVoiceId(null);
    setPlayingMessageId(st.msgId);
  };

  // 16-bit little-endian PCM を AudioBuffer にして途切れなく連続再生
  const schedulePcmBytes = (bytes: Uint8Array, sampleRate: number, lead = 0.05) => {
    const ctx = ensureAudioContext();
    if (!ctx) return;
    const frames = Math.floor(bytes.length / 2);
    if (frames === 0) return;
    const buffer = ctx.createBuffer(1, frames, sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < frames; i++) {
      let v = bytes[2 * i] | (bytes[2 * i + 1] << 8);
      if (v >= 0x8000) v -= 0x10000;
      channel[i] = v / 32768;
    }

    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime + lead, nextPlayTimeRef.current);
    src.start(startAt);
    nextPlayTimeRef.current = startAt + buffer.duration;
    liveSourcesRef.current.push(src);
    src.onended = () => {
      liveSourcesRef.current = liveSourcesRef.current.filter((s) => s !== src);
      if (liveSourcesRef.current.length === 0 && liveSpeakRef.current?.done) {
        liveSpeakRef.current = null;
        setPlayingMessageId(null);
      }
    };
  };

  // Live から届いた 16-bit PCM (既定 24kHz) チャンクを途切れなく連続再生
  const playLivePcmChunk = (b64: string, mimeType?: string) => {
    const st = liveSpeakRef.current;
    if (!st) return; // 読み上げ依頼中以外の音声は破棄（古い応答の混入防止）
    if (!ensureAudioContext()) return;
    markSpeakStarted(st);

    const rateMatch = /rate=(\d+)/.exec(mimeType || '');
    const sampleRate = rateMatch ? Number(rateMatch[1]) : 24000;
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    schedulePcmBytes(bytes, sampleRate);
  };

  // Gemini TTS ストリーミング (/api/tts/stream) で、合成完了を待たず先頭から逐次再生する。
  // エージェントごとの固定声を維持。失敗時は /api/tts（Neural2 フォールバック付き）→ ブラウザ音声の順に切り替える。
  const playTtsStream = async (text: string, activeId: string, agentId?: string) => {
    stopAudio();
    const st: NonNullable<typeof liveSpeakRef.current> = { text, msgId: activeId, gotAudio: false, done: false, timer: null };
    liveSpeakRef.current = st;
    setLoadingVoiceId(activeId);
    if (!ensureAudioContext()) {
      liveSpeakRef.current = null;
      playTtsAudio(text, activeId, agentId);
      return;
    }

    const FLUSH_BYTES = 4800; // 0.1 秒分 (24kHz × 2byte) ずつまとめて再生キューへ
    try {
      const res = await fetch('/api/tts/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, agent_id: agentId }),
      });
      if (liveSpeakRef.current !== st) return; // 別の再生に切り替わった
      if (!res.ok || !res.body) throw new Error(`stream tts status ${res.status}`);

      const sampleRate = Number(res.headers.get('X-Sample-Rate')) || 24000;
      const reader = res.body.getReader();
      let pending = new Uint8Array(0);
      const flush = (all: boolean) => {
        let len = all ? pending.length : pending.length >= FLUSH_BYTES ? pending.length : 0;
        len -= len % 2; // 16-bit 境界に揃え、端数バイトは次チャンクへ持ち越す
        if (len <= 0) return;
        markSpeakStarted(st);
        schedulePcmBytes(pending.subarray(0, len), sampleRate, nextPlayTimeRef.current ? 0.05 : 0.12);
        pending = pending.slice(len);
      };

      for (;;) {
        const { done, value } = await reader.read();
        if (liveSpeakRef.current !== st) {
          reader.cancel().catch(() => {});
          return;
        }
        if (done) break;
        if (value && value.length) {
          const merged = new Uint8Array(pending.length + value.length);
          merged.set(pending);
          merged.set(value, pending.length);
          pending = merged;
          flush(false);
        }
      }
      flush(true);

      if (!st.gotAudio) throw new Error('stream tts returned no audio');
      st.done = true;
      if (liveSourcesRef.current.length === 0) {
        liveSpeakRef.current = null;
        setPlayingMessageId(null);
      }
    } catch (err) {
      if (liveSpeakRef.current !== st) return;
      if (st.gotAudio) {
        // 再生途中で途切れた場合は、再生済み分の終了を待って終える（重複読み上げを避ける）
        st.done = true;
        if (liveSourcesRef.current.length === 0) {
          liveSpeakRef.current = null;
          setPlayingMessageId(null);
        }
        return;
      }
      console.warn('Streaming TTS failed, fallback to /api/tts:', err);
      liveSpeakRef.current = null;
      playTtsAudio(text, activeId, agentId);
    }
  };

  // Live で音声が得られなかった場合に従来 TTS (/api/tts) へ切り替え
  const fallbackLiveToTts = () => {
    const st = liveSpeakRef.current;
    if (!st) return;
    if (st.timer) clearTimeout(st.timer);
    liveSpeakRef.current = null;
    playTtsAudio(st.text, st.msgId);
  };

  // Live のターン完了: 音声未着なら TTS へ、再生中なら再生終了を待って状態をクリア
  const handleLiveTurnComplete = () => {
    const st = liveSpeakRef.current;
    if (!st) return;
    if (!st.gotAudio) {
      fallbackLiveToTts();
      return;
    }
    st.done = true;
    if (liveSourcesRef.current.length === 0) {
      liveSpeakRef.current = null;
      setPlayingMessageId(null);
    }
  };

  // 音声停止
  const stopAudio = () => {
    stopLivePlayback();
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    setPlayingMessageId(null);
    setLoadingVoiceId(null);
  };

  // ブラウザ標準TTSフォールバック
  const fallbackSpeech = (textToSpeak: string) => {
    setLoadingVoiceId(null);
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      const cleanText = textToSpeak.replace(/[*#【】]/g, '').replace(/https?:\/\/\S+/g, '');
      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.lang = 'ja-JP';
      utterance.rate = 1.03;
      utterance.onend = () => setPlayingMessageId(null);
      utterance.onerror = () => setPlayingMessageId(null);
      window.speechSynthesis.speak(utterance);
    } else {
      setPlayingMessageId(null);
    }
  };

  // 高品質音声読み上げ (Google Cloud Neural2 TTS & 高速自然日本語スクリプト)
  const speakText = async (text: string, msgId?: string, agentId?: string) => {
    const activeId = msgId || String(Date.now());
    if ((playingMessageId === activeId || loadingVoiceId === activeId) && msgId) {
      stopAudio();
      return;
    }
    await playTtsStream(text, activeId, agentId);
  };

  // /api/tts による読み上げ本体（state を参照しないため WebSocket コールバックからも安全に呼べる）
  // agentId を渡すと、エージェントごとに固定された声で読み上げる
  const playTtsAudio = async (text: string, activeId: string, agentId?: string) => {
    stopAudio();
    setLoadingVoiceId(activeId);

    try {
      const res = await fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, agent_id: agentId }),
      });
      const data = await res.json();

      if (data.status === 'success' && data.audio_content) {
        const audio = new Audio(`data:${data.mime_type || 'audio/mp3'};base64,${data.audio_content}`);
        currentAudioRef.current = audio;

        audio.onended = () => {
          setPlayingMessageId(null);
          setLoadingVoiceId(null);
          currentAudioRef.current = null;
        };
        audio.onerror = () => {
          setPlayingMessageId(null);
          setLoadingVoiceId(null);
          fallbackSpeech(data.speech_text || text);
        };

        setPlayingMessageId(activeId);
        setLoadingVoiceId(null);
        await audio.play();
      } else {
        fallbackSpeech(data.speech_text || text);
      }
    } catch (err) {
      console.warn('TTS API error, fallback to browser speech:', err);
      fallbackSpeech(text);
    } finally {
      setLoadingVoiceId(null);
    }
  };

  // メッセージ送信（セッション＆画像対応）
  const handleSendMessage = async (
    queryToSend?: string,
    targetSessionId?: string,
    imageToSend?: string | null,
    isVoiceOrigin: boolean = false
  ) => {
    const text = queryToSend || inputText.trim();
    const currentImg = imageToSend !== undefined ? imageToSend : capturedImage;
    if ((!text && !currentImg) || isLoading) return;

    const actualText = text || (currentImg ? '【現場写真添付】撮影された画像を解析してください。' : '');
    const sessId = targetSessionId || activeSessionId || 'web-session';
    setActiveSessionId(sessId);
    setInputText('');
    setCapturedImage(null);

    const userMsg: ChatMessage = {
      id: String(Date.now()),
      sessionId: sessId,
      sender: 'user',
      text: actualText,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      imageData: currentImg || undefined,
    };

    setMessages((prev) => [...prev, userMsg]);
    setActiveReasoningMessage(null);
    setIsLoading(true);
    setLoadingPhase(1);
    setIsAgentAssigned(false);

    // 同じ会話で既に専門AIが担当している続きの質問（写真なし）は、ルーティング演出を省き担当が考える演出にする
    const prevAgentId = [...messagesRef.current]
      .reverse()
      .find((m) => m.sender === 'ai' && m.sessionId === sessId && m.assignedAgent && m.assignedAgent !== 'aiops_orchestrator')
      ?.assignedAgent;
    const expectFollowup = Boolean(prevAgentId) && !currentImg;

    // 3. 呼び出し演出の担当は API の実判定（assigned_agent）で確定させる。
    //    推測で別の専門AIを表示しないよう、確定までは「判定中」とする（シナリオ選択時はシナリオの担当を表示済み）
    if (expectFollowup) {
      setCallingAgentId(prevAgentId!);
    } else if (!sessId.startsWith('scenario-')) {
      setCallingAgentId(null);
    }

    let animTimers: number[] = [];
    const clearAnimTimers = () => {
      animTimers.forEach((t) => clearTimeout(t));
      animTimers = [];
    };
    // ルーティング演出（5.6秒）
    //   0〜1.0秒: 1/3 総合受付解析 / 1.0〜3.6秒: 2/3 サブエージェント呼出（ルーティング）
    //   3.6〜4.6秒: 2/3 アサイン確定（誰に振られたかを 1 秒見せる） / 4.6〜5.6秒: 3/3 ログ照合・手順策定
    const startRoutingAnimation = (): Promise<void> => {
      clearAnimTimers();
      setLoadingMode('routing');
      setIsAgentAssigned(false);
      setLoadingPhase(1);
      animTimers.push(window.setTimeout(() => setLoadingPhase(2), 1000));
      animTimers.push(window.setTimeout(() => setIsAgentAssigned(true), 3600));
      animTimers.push(window.setTimeout(() => setLoadingPhase(3), 4600));
      return new Promise((resolve) => animTimers.push(window.setTimeout(resolve, 5600)));
    };
    // 担当の専門AIが考える演出（1〜3秒でランダム）
    const startThinkingAnimation = (): Promise<void> => {
      clearAnimTimers();
      setLoadingMode('thinking');
      const ms = 1000 + Math.floor(Math.random() * 2000);
      return new Promise((resolve) => animTimers.push(window.setTimeout(resolve, ms)));
    };

    let animation = expectFollowup ? startThinkingAnimation() : startRoutingAnimation();

    try {
      // 読み上げ中表示と一致させるため、AI メッセージ ID を先に採番する
      const aiMsgId = String(Date.now() + 1);
      const fetchPromise = fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: actualText, session_id: sessId, image_data: currentImg }),
      })
        .then((res) => res.json())
        .then((d) => {
          if (d?.assigned_agent) {
            setCallingAgentId(d.assigned_agent);
          }
          // 音声ボタンで質問した時のみ音声で返答（テキスト入力やボタンクリック時は音声を出さない）。
          // 演出の終了を待たず、回答が届いた時点でエージェント固有の声でストリーミング読み上げを開始する
          if (isVoiceOrigin && d?.answer) {
            playTtsStream(d.answer, aiMsgId, d.assigned_agent);
          }
          return d;
        });

      const data = await fetchPromise;
      // 続きの質問のつもりが別の担当へ振り直された場合は、その時点からルーティング演出に切り替える
      if (expectFollowup && !(data?.is_followup && data?.assigned_agent === prevAgentId)) {
        animation = startRoutingAnimation();
      }
      await animation;

      const aiMsg: ChatMessage = {
        id: aiMsgId,
        sessionId: data.session_id || sessId,
        sender: 'ai',
        text: data.answer,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        assignedAgent: data.assigned_agent,
        assignedAgentLabel: data.assigned_agent_label,
        reasoningSteps: data.reasoning_steps,
        tickets: data.tickets,
        logs: data.logs,
        runbook: data.runbook,
        escalationTeam: data.escalation_team,
        promptToUser: data.prompt_to_user,
        suggestedFollowups: data.suggested_followups || [],
        technicalDetails: data.technical_details,
      };

      setMessages((prev) => [...prev, aiMsg]);
      setActiveReasoningMessage(aiMsg);
      if (data.session_id) {
        setActiveSessionId(data.session_id);
      }
    } catch (err) {
      console.error('Chat error:', err);
      const errorMsg: ChatMessage = {
        id: String(Date.now() + 1),
        sender: 'ai',
        text: '通信エラーが発生しました。しばらく待ってから再度お試しください。',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      clearAnimTimers();
      setIsLoading(false);
      setLoadingPhase(0);
      setIsAgentAssigned(false);
      setLoadingMode('routing');
    }
  };

  useEffect(() => {
    handleSendMessageRef.current = handleSendMessage;
  });

  const handleSelectScenario = (scenario: Scenario) => {
    setSelectedScenarioId(scenario.id);
    const scenarioSessionId = `scenario-${scenario.id}-${Date.now()}`;
    if (scenario.agent_id) {
      setCallingAgentId(scenario.agent_id);
    }
    handleSendMessage(scenario.prompt, scenarioSessionId);
  };

  const handleEscalate = (team: string) => {
    const confirmMsg: ChatMessage = {
      id: String(Date.now()),
      sender: 'user',
      text: `解決しないため、有人サポート（${team}）へ引き継ぎをお願いします。`,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };
    setMessages((prev) => [...prev, confirmMsg]);

    setTimeout(() => {
      const ackMsg: ChatMessage = {
        id: String(Date.now() + 1),
        sender: 'ai',
        text: `【引き継ぎ完了】有人サポートデスク（${team}）へトラブルカルテを発行しました。\nこれまでの調査内容や機器の状態をすべて共有済みです。担当オペレーターよりまもなくご連絡いたしますので、そのまま少々お待ちください。`,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        assignedAgentLabel: '総合受付 AIオーケストレーター',
      };
      setMessages((prev) => [...prev, ackMsg]);
    }, 500);
  };

  const currentReasoning = isLoading
    ? null
    : (activeReasoningMessage?.reasoningSteps ||
       messages.filter((m) => m.reasoningSteps).slice(-1)[0]?.reasoningSteps);

  return (
    <div className="flex flex-col h-full h-[100dvh] w-full max-w-full bg-slate-50 text-slate-800 overflow-hidden font-sans">
      {/* 1. 明るいトップナビゲーション */}
      <header className="flex-none bg-white border-b border-slate-200 px-3 sm:px-4 py-2 sm:py-3 shadow-xs z-10 w-full pt-safe">
        <div className={`mx-auto flex items-center justify-between gap-2 transition-all duration-300 ${isKarteOpen ? 'max-w-6xl' : 'max-w-4xl'}`}>
          <div className="flex items-center space-x-2 sm:space-x-3 shrink-0 min-w-0">
            <div className="w-8 h-8 sm:w-10 sm:h-10 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-600 flex items-center justify-center text-white shadow-md shadow-blue-500/20 shrink-0">
              <span className="text-lg sm:text-xl">🏪</span>
            </div>
            <div className="min-w-0">
              <div className="flex items-center space-x-2">
                <h1 className="text-sm sm:text-base md:text-lg font-bold text-slate-900 tracking-tight truncate">
                  店舗・システム運用 AI
                </h1>
                <span className="hidden md:inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 mr-1.5 animate-pulse"></span>
                  自動診断稼働中
                </span>
              </div>
              <p className="text-xs text-slate-500 hidden sm:block truncate">
                現場スタッフの困りごとをAIが素早く一次切り分け・解決支援します
              </p>
            </div>
          </div>

          {/* モバイル用タブラベル */}
          <div className="flex md:hidden bg-slate-100 p-0.5 rounded-xl border border-slate-200 shrink-0">
            <button
              onClick={() => {
                setActiveTab('chat');
                setIsKarteOpen(false);
              }}
              className={`px-2 py-1 text-xs font-bold rounded-lg transition-all ${
                !isKarteOpen && activeTab === 'chat' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-600'
              }`}
            >
              💬 チャット
            </button>
            <button
              onClick={() => {
                setActiveTab('reasoning');
                setIsKarteOpen(true);
              }}
              className={`px-2 py-1 text-xs font-bold rounded-lg transition-all ${
                isKarteOpen && activeTab === 'reasoning' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-600'
              }`}
            >
              📋 カルテ
            </button>
            <button
              onClick={() => {
                setActiveTab('knowledge');
                setIsKarteOpen(true);
              }}
              className={`px-2 py-1 text-xs font-bold rounded-lg transition-all ${
                isKarteOpen && activeTab === 'knowledge' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-600'
              }`}
            >
              📚 事例集
            </button>
          </div>

          {/* デスクトップ用ステータス & カルテトグルボタン (モバイルでは非表示) */}
          <div className="hidden md:flex items-center space-x-2 text-xs shrink-0">
            <div className="hidden lg:flex items-center space-x-2">
              <span className="bg-slate-100 text-slate-700 font-medium px-2.5 py-1.5 rounded-lg border border-slate-200 flex items-center gap-1.5">
                <div className="flex -space-x-1.5 overflow-hidden">
                  <img src="/agents/infra_specialist.jpg" alt="" className="inline-block w-4 h-4 rounded-full ring-1 ring-white object-cover" />
                  <img src="/agents/order_specialist.jpg" alt="" className="inline-block w-4 h-4 rounded-full ring-1 ring-white object-cover" />
                  <img src="/agents/network_specialist.jpg" alt="" className="inline-block w-4 h-4 rounded-full ring-1 ring-white object-cover" />
                  <img src="/agents/pos_specialist.jpg" alt="" className="inline-block w-4 h-4 rounded-full ring-1 ring-white object-cover" />
                </div>
                <span>4つの専門AIが待機中</span>
              </span>
              <span className={`bg-gradient-to-r ${isLiveConnected ? 'from-emerald-50 to-indigo-50 border-emerald-300 text-emerald-800' : 'from-blue-50 to-indigo-50 border-indigo-200 text-indigo-700'} font-semibold px-3 py-1.5 rounded-lg border flex items-center gap-1.5 shadow-2xs`}>
                <span className={`inline-block w-2 h-2 rounded-full ${isLiveConnected ? 'bg-emerald-500 animate-pulse' : 'bg-blue-400'}`}></span>
                <span>⚡ Gemini Live ({connectedLiveModel})</span>
                <span className="text-[10px] text-slate-500 font-medium border-l border-indigo-200 pl-1.5">
                  {isLiveConnected ? 'Live接続中' : 'リアルタイム音声'}
                </span>
              </span>
            </div>

            {/* カルテトグルボタン */}
            <button
              onClick={() => {
                setIsKarteOpen((prev) => !prev);
                if (!isKarteOpen) {
                  setActiveTab('reasoning');
                }
              }}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all flex items-center space-x-1.5 border shadow-2xs ${
                isKarteOpen
                  ? 'bg-blue-600 text-white border-blue-600 shadow-blue-500/20 ring-2 ring-blue-500/30'
                  : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50 hover:border-slate-400'
              }`}
              title={isKarteOpen ? "診断カルテを閉じる" : "AIの診断カルテを開く"}
            >
              <span>📋</span>
              <span>{isKarteOpen ? 'カルテを閉じる' : 'カルテ'}</span>
              {isKarteOpen && <span className="text-[10px] ml-0.5 opacity-80">✕</span>}
            </button>
          </div>
        </div>
      </header>

      {/* 2. デモシナリオ プリセットバー */}
      <div className="flex-none bg-white border-b border-slate-200 px-3 sm:px-4 py-2 sm:py-2.5 overflow-x-auto shadow-2xs w-full max-w-full">
        <div className={`mx-auto flex items-center space-x-2 min-w-max transition-all duration-300 ${isKarteOpen ? 'max-w-6xl' : 'max-w-4xl'}`}>
          <span className="text-xs font-bold text-slate-500 mr-1 flex items-center shrink-0">
            <span className="hidden sm:inline">👉 </span>ワンクリックでお試し:
          </span>
          {scenarios.map((sc) => (
            <button
              key={sc.id}
              onClick={() => handleSelectScenario(sc)}
              className={`text-xs px-2.5 sm:px-3.5 py-1.5 sm:py-2 rounded-xl border transition-all flex items-center space-x-1.5 sm:space-x-2 font-medium shrink-0 ${
                selectedScenarioId === sc.id
                  ? 'bg-blue-50 border-blue-400 text-blue-800 shadow-sm ring-2 ring-blue-500/20'
                  : 'bg-slate-50 border-slate-200 text-slate-700 hover:bg-slate-100 hover:border-slate-300'
              }`}
              title={sc.description}
            >
              <span className="text-xs sm:text-sm">{sc.badge ? sc.badge.split(' ')[0] : '📌'}</span>
              <span>{sc.title}</span>
            </button>
          ))}
        </div>
      </div>

      {/* 3. メインコンテンツ */}
      <main ref={mainRef} className={`flex-1 flex overflow-hidden mx-auto w-full max-w-full min-w-0 transition-all duration-300 ${isKarteOpen ? 'max-w-6xl' : 'max-w-4xl'}`}>
        {/* 左ペイン: チャット画面 */}
        <section
          className={`flex-1 flex flex-col h-full min-w-0 max-w-full bg-slate-50/70 border-x border-slate-200/80 ${
            isKarteOpen ? 'hidden md:flex' : 'flex'
          }`}
        >
          {(isVoiceActive || isAnalyzingVoice) && (
            <div className={`flex-none border-b px-4 py-2.5 flex items-center justify-between transition-all ${
              isAnalyzingVoice
                ? 'bg-indigo-50 border-indigo-200 text-indigo-950'
                : 'bg-blue-50 border-blue-200 text-blue-900'
            }`}>
              <div className="flex items-center space-x-2.5 min-w-0">
                {isAnalyzingVoice ? (
                  <span className="w-4 h-4 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin shrink-0" />
                ) : (
                  <span className="flex items-center space-x-1 shrink-0">
                    <span className="w-1.5 h-3 bg-blue-500 rounded-full animate-wave" style={{ animationDelay: '0.1s' }} />
                    <span className="w-1.5 h-5 bg-blue-600 rounded-full animate-wave" style={{ animationDelay: '0.3s' }} />
                    <span className="w-1.5 h-4 bg-indigo-500 rounded-full animate-wave" style={{ animationDelay: '0.2s' }} />
                  </span>
                )}
                <div className="flex items-center gap-2 min-w-0">
                  <span className="bg-indigo-600 text-white text-[10px] font-extrabold px-1.5 py-0.5 rounded tracking-wide shrink-0">
                    LIVE
                  </span>
                  <span className="text-xs font-bold truncate text-indigo-950">
                    {liveOutputTranscript ? `🎙️ リアルタイム応答: ${liveOutputTranscript}` : voiceStatus}
                  </span>
                </div>
              </div>
              {!isAnalyzingVoice && (
                <button
                  type="button"
                  onClick={cancelVoiceInput}
                  className="text-xs font-bold px-3 py-1 bg-rose-50 text-rose-700 border border-rose-200 rounded-lg hover:bg-rose-100 transition-all shrink-0"
                  title="音声を破棄して終了"
                >
                  キャンセル
                </button>
              )}
            </div>
          )}

          {/* メッセージリスト */}
          <div ref={chatContainerRef} className="flex-1 overflow-y-auto px-3 sm:px-6 py-4 sm:py-6 w-full min-w-0 scroll-smooth">
            <div className="max-w-3xl mx-auto space-y-4 sm:space-y-6 w-full min-w-0">
              {messages.map((msg) => {
                const avatar = getAgentAvatar(msg.assignedAgent, msg.assignedAgentLabel);

                if (msg.sender === 'user') {
                  return (
                    <div
                      key={msg.id}
                      id={`message-${msg.id}`}
                      className="flex flex-col items-end w-full min-w-0 scroll-mt-2 sm:scroll-mt-4"
                    >
                      <div className="flex items-center space-x-2 mb-1.5 px-1 max-w-full">
                        <span className="text-xs font-bold text-slate-500">🧑‍💼 現場スタッフ</span>
                        <span className="text-[11px] text-slate-400 shrink-0">{msg.timestamp}</span>
                      </div>
                      <div className="max-w-[90%] sm:max-w-xl bg-slate-100 border border-slate-300 text-slate-900 rounded-2xl rounded-tr-none p-3.5 sm:p-4 shadow-xs break-words min-w-0">
                        {msg.imageData && (
                          <div className="mb-2 max-w-full">
                            <img
                              src={msg.imageData}
                              alt="撮影画像"
                              className="max-h-48 max-w-full rounded-xl border border-slate-300 object-cover"
                            />
                          </div>
                        )}
                        <div className="text-sm sm:text-base leading-relaxed whitespace-pre-line font-medium text-slate-900">
                          {msg.text}
                        </div>
                      </div>
                    </div>
                  );
                }

                return (
                  <div
                    key={msg.id}
                    id={`message-${msg.id}`}
                    className="flex items-start gap-2.5 sm:gap-3.5 w-full min-w-0 scroll-mt-2 sm:scroll-mt-4 group"
                  >
                    {/* 大きなエージェントイラストアイコン */}
                    <div className="flex flex-col items-center shrink-0 pt-0.5 select-none">
                      <div className="relative">
                        <img
                          src={avatar.icon}
                          alt={avatar.name}
                          className="w-11 h-11 sm:w-13 sm:h-13 rounded-full object-cover border-2 border-white shadow-md ring-2 ring-slate-200/90 transition-transform group-hover:scale-105"
                        />
                        <span className="absolute bottom-0 right-0 w-3.5 h-3.5 rounded-full bg-emerald-500 border-2 border-white" title="担当AI対応中"></span>
                      </div>
                      <span className="text-[10px] font-bold text-slate-600 mt-1 scale-90 sm:scale-100 origin-top text-center truncate max-w-[56px] sm:max-w-[64px]">
                        {avatar.shortName}
                      </span>
                    </div>

                    {/* 右側: 名前ヘッダー ＋ メイン吹き出しバブル ＋ サジェスト */}
                    <div className="flex-1 min-w-0 flex flex-col">
                      {/* 送信者名ヘッダー */}
                      <div className="flex items-center space-x-2 mb-1.5 px-0.5 max-w-full">
                        <span className="text-xs sm:text-sm font-bold text-slate-900 truncate">
                          {msg.assignedAgentLabel || avatar.name}
                        </span>
                        <span className="text-[11px] text-slate-400 shrink-0">{msg.timestamp}</span>
                      </div>

                      {/* 吹き出しバブル */}
                      <div className="w-full max-w-full bg-white border border-slate-200/90 text-slate-900 rounded-2xl rounded-tl-sm p-4 sm:p-5 shadow-xs shadow-slate-100/80 break-words min-w-0">
                        {/* 現場写真 */}
                        {msg.imageData && (
                          <div className="mb-3 max-w-full">
                            <img
                              src={msg.imageData}
                              alt="現場撮影写真"
                              className="max-h-56 max-w-full rounded-xl border border-slate-300 shadow-xs object-cover cursor-pointer hover:opacity-95 transition-opacity"
                              onClick={() => window.open(msg.imageData, '_blank')}
                              title="クリックして全画面表示"
                            />
                            <div className="text-[11px] text-slate-500 mt-1 flex items-center font-medium">
                              <span className="mr-1">📷</span> 現場撮影写真（AI解析対象）
                            </div>
                          </div>
                        )}

                        {/* 構造化された回答本文 */}
                        <StructuredAnswer text={msg.text} />

                        {/* 補足アコーディオン（類似事例・技術詳細） */}
                        {((msg.tickets && msg.tickets.length > 0) || msg.technicalDetails) && (
                          <div className="mt-3.5 space-y-1.5 pt-2.5 border-t border-slate-100">
                            {/* 過去事例 */}
                            {msg.tickets && msg.tickets.length > 0 && (
                              <details className="group rounded-xl border border-amber-200/80 bg-amber-50/40 text-xs overflow-hidden transition-all">
                                <summary className="flex items-center justify-between px-3 py-2 cursor-pointer font-medium text-amber-950 hover:bg-amber-50/80 select-none">
                                  <span className="flex items-center gap-1.5 truncate pr-2">
                                    <span>📖</span>
                                    <span className="font-bold">類似インシデント事例:</span>
                                    <span className="truncate text-slate-700">{msg.tickets[0].title}</span>
                                  </span>
                                  <span className="shrink-0 flex items-center gap-1.5">
                                    <span className="text-[10px] font-bold text-emerald-800 bg-emerald-100 px-1.5 py-0.5 rounded">
                                      {((msg.tickets[0].similarity_score || 0.95) * 100).toFixed(0)}% 一致
                                    </span>
                                    <span className="text-[10px] text-amber-700 group-open:rotate-180 transition-transform">▼</span>
                                  </span>
                                </summary>
                                <div className="p-3 pt-2 border-t border-amber-100 bg-white/95 space-y-1.5">
                                  <div className="text-slate-600 text-xs">
                                    <span className="font-bold text-slate-800">当時の原因:</span> {msg.tickets[0].root_cause}
                                  </div>
                                  <div className="text-emerald-900 text-xs bg-emerald-50/80 p-2 rounded-lg border border-emerald-100 leading-relaxed">
                                    <span className="font-bold">当時の解決策:</span> {msg.tickets[0].resolution}
                                  </div>
                                </div>
                              </details>
                            )}

                            {/* 技術診断詳細 */}
                            {msg.technicalDetails && (
                              <details className="group rounded-xl border border-slate-200 bg-slate-50/60 text-xs overflow-hidden transition-all">
                                <summary className="flex items-center justify-between px-3 py-2 cursor-pointer font-medium text-slate-700 hover:bg-slate-100 select-none">
                                  <span className="flex items-center gap-1.5">
                                    <span>🔧</span>
                                    <span>技術詳細・システム診断情報</span>
                                  </span>
                                  <span className="text-[10px] text-slate-400 group-open:rotate-180 transition-transform">▼</span>
                                </summary>
                                <div className="p-3 pt-2 border-t border-slate-200 font-mono text-[11px] leading-relaxed text-slate-700 whitespace-pre-wrap bg-white">
                                  {msg.technicalDetails}
                                </div>
                              </details>
                            )}
                          </div>
                        )}

                        {/* クイックアクションバー (音声 / カルテ / 有人エスカレーション) */}
                        <div className="mt-3.5 pt-2.5 border-t border-slate-100 flex flex-wrap items-center justify-between gap-2 text-xs">
                          <div className="flex items-center gap-2">
                            {msg.reasoningSteps && (
                              <button
                                onClick={() => {
                                  setActiveReasoningMessage(msg);
                                  setActiveTab('reasoning');
                                  setIsKarteOpen(true);
                                }}
                                className="font-bold text-blue-600 hover:text-blue-800 bg-blue-50/80 hover:bg-blue-100/80 px-2.5 py-1 rounded-lg transition-colors flex items-center gap-1"
                              >
                                <span>🔍</span>
                                <span>調査カルテ（{msg.reasoningSteps.length}ステップ）</span>
                              </button>
                            )}
                            <button
                              onClick={() => speakText(msg.text, msg.id, msg.assignedAgent)}
                              disabled={loadingVoiceId === msg.id}
                              className={`px-2.5 py-1 rounded-lg transition-all flex items-center gap-1 text-xs font-semibold ${
                                playingMessageId === msg.id
                                  ? 'bg-blue-600 text-white shadow-2xs animate-pulse'
                                  : loadingVoiceId === msg.id
                                  ? 'bg-blue-100 text-blue-700 animate-pulse cursor-wait'
                                  : 'text-slate-600 hover:text-blue-600 hover:bg-slate-100'
                              }`}
                              title={
                                playingMessageId === msg.id
                                  ? "再生を停止"
                                  : loadingVoiceId === msg.id
                                  ? "音声を準備しています"
                                  : "高品質AI音声で聞く"
                              }
                            >
                              <span>
                                {playingMessageId === msg.id
                                  ? '⏹️ 音声停止'
                                  : loadingVoiceId === msg.id
                                  ? '⏳ 音声準備中...'
                                  : '🔊 声で聞く'}
                              </span>
                            </button>
                          </div>

                          {msg.escalationTeam && (
                            <button
                              onClick={() => handleEscalate(msg.escalationTeam!)}
                              className="text-xs font-bold px-3 py-1 bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-lg transition-all flex items-center gap-1 shrink-0"
                            >
                              <span>📞</span>
                              <span>{msg.escalationTeam} へ電話・連絡</span>
                            </button>
                          )}
                        </div>
                      </div>

                      {/* 更問サジェストチップ（AIバブルの直下に配置） */}
                      {msg.suggestedFollowups && msg.suggestedFollowups.length > 0 && (() => {
                        const targetSessId = msg.sessionId || `session-${msg.id}`;
                        const isThisMsgRecording = isRecording && recordingSessionId === targetSessId;

                        return (
                          <div className="mt-2.5 flex flex-wrap items-center gap-1.5 w-full pl-0.5">
                            <span className="text-[11px] font-semibold text-slate-400 flex items-center gap-1 mr-1">
                              <span>💡</span>
                              <span>続けて質問:</span>
                            </span>

                            {/* 更問い専用 音声入力ボタン (Push-to-Talk) */}
                            <button
                              type="button"
                              onPointerDown={(e) => {
                                if (e.button !== 0 && e.pointerType === 'mouse') return;
                                e.preventDefault();
                                try {
                                  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                                } catch (_) {}
                                startVoiceInput(targetSessId, msg.suggestedFollowups);
                              }}
                              onPointerUp={(e) => {
                                e.preventDefault();
                                try {
                                  (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
                                } catch (_) {}
                                stopVoiceInputAndSend();
                              }}
                              onPointerCancel={(e) => {
                                try {
                                  (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
                                } catch (_) {}
                                stopVoiceInputAndSend();
                              }}
                              onContextMenu={(e) => e.preventDefault()}
                              disabled={isLoading}
                              className={`select-none touch-none text-xs font-bold px-3 py-1.5 rounded-full transition-all flex items-center gap-1.5 cursor-pointer shadow-xs ${
                                isThisMsgRecording
                                  ? 'bg-rose-600 border border-rose-700 text-white animate-pulse shadow-md ring-2 ring-rose-400 scale-105'
                                  : 'bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white border border-blue-500 shadow-blue-500/20 active:scale-95'
                              }`}
                              title={
                                isThisMsgRecording
                                  ? '指/マウスを離すと、担当専門AIとそのまま自然にLive会話します'
                                  : 'ボタンを押したまま話すと、担当専門AIとそのまま自然にLive会話します'
                              }
                            >
                              <span className="text-sm">{isThisMsgRecording ? '🔴' : '🎙️'}</span>
                              <span>{isThisMsgRecording ? '録音中 (離して送信)' : '音声で質問'}</span>
                            </button>

                            {/* 更問い専用 テキスト入力ボタン */}
                            <button
                              type="button"
                              onClick={() => focusTextInput('', targetSessId)}
                              disabled={isLoading}
                              className="text-xs font-semibold px-2.5 py-1.5 rounded-full transition-all flex items-center gap-1 cursor-pointer bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 shadow-2xs active:scale-95 disabled:opacity-50"
                              title="テキスト入力欄へ移動して自由に更問いを入力します"
                            >
                              <span className="text-xs">✏️</span>
                              <span>テキスト入力</span>
                            </button>

                            {msg.suggestedFollowups.map((fText, fIdx) => {
                              const isSelected = false;
                              return (
                                <div
                                  key={fIdx}
                                  className={`inline-flex items-center rounded-full transition-all shadow-2xs border ${
                                    isSelected
                                      ? 'bg-blue-600 text-white font-bold ring-4 ring-blue-300 shadow-md animate-pulse scale-105 border-blue-600'
                                      : 'bg-white hover:bg-blue-50/70 border-slate-200 hover:border-blue-300 text-slate-700'
                                  }`}
                                >
                                  <button
                                    type="button"
                                    onClick={() => handleSendMessage(fText, targetSessId)}
                                    disabled={isLoading}
                                    className="text-xs font-medium pl-3 pr-1.5 py-1.5 flex items-center gap-1 cursor-pointer disabled:opacity-50 hover:text-blue-700"
                                    title="この内容でそのまま質問を送信"
                                  >
                                    <span>{fText}</span>
                                    <span className={`text-[10px] ${isSelected ? 'text-white font-bold' : 'text-slate-400'}`}>
                                      {isSelected ? '✓ 割当' : '→'}
                                    </span>
                                  </button>
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      focusTextInput(fText, targetSessId);
                                    }}
                                    disabled={isLoading}
                                    className={`pr-2.5 pl-1 py-1.5 text-[11px] cursor-pointer transition-colors disabled:opacity-50 ${
                                      isSelected ? 'text-blue-200 hover:text-white' : 'text-slate-400 hover:text-blue-600'
                                    }`}
                                    title="この文言を入力欄にセットして編集・追記してから送信"
                                  >
                                    <span>✏️</span>
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        );
                      })()}
                    </div>
                  </div>
                );
              })}

            {isLoading && (() => {
              // 担当は API の実判定で確定する。未確定の間は「判定中」を表示し、推測の専門AIは出さない
              const isAgentResolved = Boolean(callingAgentId);
              const resolvedAvatar = callingAgentId ? getAgentAvatar(callingAgentId) : null;
              const targetAvatar = resolvedAvatar ?? {
                ...AGENT_AVATARS.aiops_orchestrator,
                name: '担当の専門AI',
                shortName: '判定中',
              };
              const isTargetActive = loadingPhase >= 2 && isAgentResolved;
              // ルーティング完了直後の 1 秒間、担当の専門AIへのアサイン確定を強調表示する
              const isAssignedView = loadingPhase === 2 && isAgentAssigned && isAgentResolved;

              // 続きの質問: ルーティングは行わず、担当済みの専門AIが考えている様子だけを見せる
              if (loadingMode === 'thinking' && resolvedAvatar) {
                return (
                  <div className="flex items-start gap-2.5 max-w-lg w-full min-w-0">
                    <img
                      src={resolvedAvatar.icon}
                      alt=""
                      className="w-10 h-10 sm:w-11 sm:h-11 rounded-full object-cover border-2 border-white shadow-sm ring-2 ring-blue-400 shrink-0"
                    />
                    <div className="bg-white border border-blue-200/90 rounded-2xl rounded-tl-sm px-4 py-3 shadow-md shadow-blue-500/5 min-w-0">
                      <div className="text-[11px] font-bold text-blue-900 truncate">{resolvedAvatar.name}</div>
                      <div className="flex items-center gap-2 mt-1 text-xs text-slate-600">
                        <span>考えています</span>
                        <span className="flex items-end gap-1 h-3" aria-hidden="true">
                          <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0s' }}></span>
                          <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0.15s' }}></span>
                          <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0.3s' }}></span>
                        </span>
                      </div>
                    </div>
                  </div>
                );
              }
              const orchestratorAvatar = AGENT_AVATARS.aiops_orchestrator;

              return (
                <div className="flex flex-col items-start space-y-2 max-w-lg w-full min-w-0">
                  <div className="bg-white border border-blue-200/90 rounded-2xl rounded-tl-sm p-4 sm:p-5 w-full space-y-3.5 shadow-md shadow-blue-500/5 min-w-0">
                    {/* ヘッダー: スピナー ＆ フェーズバッジ */}
                    <div className="flex items-center justify-between border-b border-slate-100 pb-2.5">
                      <div className="flex items-center space-x-2 text-xs font-bold text-blue-700">
                        <span className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin shrink-0"></span>
                        <span>🤖 マルチエージェント自動ディスパッチ中</span>
                      </div>
                      <span className="text-[11px] font-semibold text-blue-600 bg-blue-50 px-2.5 py-0.5 rounded-full border border-blue-100 shrink-0">
                        {loadingPhase === 1 ? '1/3 総合受付解析' : loadingPhase === 2 ? (isAssignedView ? '2/3 アサイン完了' : '2/3 サブエージェント呼出') : '3/3 ログ照合・手順策定'}
                      </span>
                    </div>

                    {/* サブエージェント呼び出しアニメーションビジュアル */}
                    <div className="bg-gradient-to-r from-slate-50 via-blue-50/40 to-slate-50 border border-slate-200/80 rounded-xl p-3 flex items-center justify-between gap-2">
                      {/* 総合受付オーケストレーター */}
                      <div className="flex flex-col items-center shrink-0">
                        <div className="relative">
                          <img
                            src={orchestratorAvatar.icon}
                            alt=""
                            className={`w-10 h-10 sm:w-11 sm:h-11 rounded-full object-cover border-2 border-white shadow-sm transition-all ${
                              loadingPhase === 1 ? 'ring-2 ring-blue-500 animate-pulse' : 'ring-1 ring-slate-200'
                            }`}
                          />
                          {loadingPhase === 1 && (
                            <span className="absolute -top-1 -right-1 flex h-3 w-3">
                              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                              <span className="relative inline-flex rounded-full h-3 w-3 bg-blue-500"></span>
                            </span>
                          )}
                        </div>
                        <span className="text-[10px] font-bold text-slate-700 mt-1 truncate max-w-[60px] text-center">
                          総合受付
                        </span>
                      </div>

                      {/* コネクタ・ルーティング進行アニメーション */}
                      <div className="flex-1 flex flex-col items-center justify-center px-1 min-w-0">
                        <span className={`text-[10px] font-bold mb-1 flex items-center gap-1 ${isAssignedView ? 'text-emerald-600' : 'text-blue-600 animate-pulse'}`}>
                          <span>{isAssignedView ? '✅' : '⚡'}</span>
                          <span className="truncate">
                            {loadingPhase === 1 ? '意図判定中' : isAssignedView ? 'アサイン完了' : 'タスク転送中'}
                          </span>
                        </span>
                        {/* 流れる矢印アニメーション（アサイン完了時はすべて点灯して停止） */}
                        <div className={`flex items-center space-x-1 font-mono text-xs ${isAssignedView ? 'text-emerald-500' : 'text-blue-500'}`}>
                          <span className={`transition-opacity ${loadingPhase >= 1 ? `opacity-100 ${isAssignedView ? '' : 'animate-pulse'}` : 'opacity-30'}`}>●</span>
                          <span className={`transition-opacity ${loadingPhase >= 2 ? `opacity-100 ${isAssignedView ? '' : 'animate-pulse'}` : 'opacity-30'}`} style={{ animationDelay: '0.2s' }}>➔</span>
                          <span className={`transition-opacity ${loadingPhase >= 2 ? `opacity-100 ${isAssignedView ? '' : 'animate-pulse'}` : 'opacity-30'}`} style={{ animationDelay: '0.4s' }}>●</span>
                          <span className={`transition-opacity ${loadingPhase >= 3 || isAssignedView ? `opacity-100 ${isAssignedView ? '' : 'animate-pulse'}` : 'opacity-30'}`} style={{ animationDelay: '0.6s' }}>➔</span>
                        </div>
                      </div>

                      {/* ターゲットの専門サブエージェント */}
                      <div className="flex flex-col items-center shrink-0">
                        <div className="relative">
                          <img
                            src={targetAvatar.icon}
                            alt=""
                            className={`w-10 h-10 sm:w-11 sm:h-11 rounded-full object-cover border-2 border-white shadow-sm transition-all duration-300 ${
                              isAssignedView
                                ? 'scale-125 ring-4 ring-emerald-500 shadow-lg shadow-emerald-500/30'
                                : isTargetActive
                                  ? 'scale-110 ring-4 ring-blue-500/80 shadow-md shadow-blue-500/20'
                                  : 'opacity-50 grayscale ring-1 ring-slate-200'
                            }`}
                          />
                          {isAssignedView ? (
                            <span className="absolute -bottom-1 -right-1 flex items-center justify-center h-4 w-4 rounded-full bg-emerald-500 border border-white text-[9px] text-white font-bold">
                              ✓
                            </span>
                          ) : isTargetActive && (
                            <span className="absolute -bottom-0.5 -right-0.5 flex h-3.5 w-3.5">
                              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                              <span className="relative inline-flex rounded-full h-3.5 w-3.5 bg-emerald-500 border border-white"></span>
                            </span>
                          )}
                        </div>
                        <span className={`text-[10px] font-bold mt-1 truncate max-w-[70px] text-center transition-colors ${
                          isAssignedView ? 'text-emerald-700 font-extrabold' : isTargetActive ? 'text-blue-900 font-extrabold' : 'text-slate-400'
                        }`}>
                          {targetAvatar.shortName}
                        </span>
                      </div>
                    </div>

                    {/* 呼び出しステータスメッセージ */}
                    <div className={`p-2.5 rounded-xl border text-xs space-y-1 transition-colors ${isAssignedView ? 'bg-emerald-50 border-emerald-300' : 'bg-slate-50/80 border-slate-200/70'}`}>
                      {(loadingPhase === 1 || (loadingPhase >= 2 && !isAgentResolved)) && (
                        <div className="flex items-center space-x-2 text-slate-700 font-medium">
                          <span className="animate-spin text-sm">🔍</span>
                          <span>総合受付 AI が現場の障害内容と対象設備を解析中...</span>
                        </div>
                      )}
                      {loadingPhase === 2 && isAgentResolved && !isAssignedView && (
                        <div className="flex items-center space-x-2 text-blue-900 font-bold animate-pulse">
                          <span className="text-sm">📞</span>
                          <span>【{targetAvatar.name}】を呼び出しています・・・</span>
                        </div>
                      )}
                      {isAssignedView && (
                        <div className="flex items-center space-x-2 text-emerald-800 font-extrabold text-sm">
                          <span>✅</span>
                          <span>【{targetAvatar.name}】にアサインしました</span>
                        </div>
                      )}
                      {loadingPhase === 3 && isAgentResolved && (
                        <div className="flex items-center space-x-2 text-emerald-900 font-bold">
                          <span className="text-sm">🔄</span>
                          <span>{targetAvatar.shortName} が過去インシデント照合＆復旧手順を策定中...</span>
                        </div>
                      )}
                    </div>

                    {/* プログレスバー */}
                    <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
                      <div
                        className="bg-blue-600 h-1.5 rounded-full transition-all duration-700 ease-out"
                        style={{ width: loadingPhase === 1 ? '33%' : loadingPhase === 2 ? (isAssignedView ? '80%' : '66%') : '100%' }}
                      ></div>
                    </div>
                  </div>
                </div>
              );
            })()}
              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* 入力フォーム */}
          <div className="flex-none p-2.5 sm:p-4 bg-white border-t border-slate-200 shadow-md w-full max-w-full pb-[max(0.625rem,env(safe-area-inset-bottom))]">
            {/* 添付画像プレビュー */}
            {capturedImage && (
              <div className="mb-2 flex items-center space-x-2.5 bg-blue-50/90 border border-blue-200 px-2.5 sm:px-3 py-1.5 rounded-xl text-xs text-blue-900 max-w-3xl mx-auto shadow-xs min-w-0">
                <img
                  src={capturedImage}
                  alt="添付画像プレビュー"
                  className="w-10 h-10 object-cover rounded-lg border border-blue-300 shadow-xs shrink-0"
                />
                <div className="flex-1 min-w-0">
                  <div className="font-bold flex items-center text-blue-950 truncate">
                    <span className="mr-1">📷</span> 現場写真を添付中
                  </div>
                  <div className="text-[10px] text-blue-700 truncate">
                    AIが外観・ランプ・画面をマルチモーダル解析
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setCapturedImage(null)}
                  className="px-2 py-1 bg-white hover:bg-rose-50 text-slate-400 hover:text-rose-600 rounded-lg text-xs font-bold border border-slate-200 transition-colors shrink-0"
                  title="画像を削除"
                >
                  ✕
                </button>
              </div>
            )}

            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSendMessage();
              }}
              className="flex items-center space-x-1.5 sm:space-x-2 max-w-3xl mx-auto w-full min-w-0"
            >
              {/* 隠しファイル入力（カメラ非対応時・ファイル選択用） */}
              <input
                type="file"
                ref={fileInputRef}
                accept="image/*"
                capture="environment"
                className="hidden"
                onChange={handleFileUpload}
              />

              {/* カメラ起動ボタン */}
              <button
                type="button"
                onClick={startCamera}
                className={`p-2.5 sm:p-3 rounded-xl sm:rounded-2xl border transition-all flex items-center justify-center shrink-0 ${
                  capturedImage
                    ? 'bg-blue-100 border-blue-300 text-blue-700 shadow-sm'
                    : 'bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200'
                }`}
                title="カメラで現場機器やエラー画面を撮影してAI診断"
              >
                <span className="text-base sm:text-lg">📷</span>
              </button>

              {/* マイク音声ボタン (Push-to-Talk: 押しっぱなしで受付け、離すと送信) */}
              <button
                type="button"
                onPointerDown={(e) => {
                  if (e.button !== 0 && e.pointerType === 'mouse') return;
                  e.preventDefault();
                  try {
                    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                  } catch (_) {}
                  startVoiceInput();
                }}
                onPointerUp={(e) => {
                  e.preventDefault();
                  try {
                    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
                  } catch (_) {}
                  stopVoiceInputAndSend();
                }}
                onPointerCancel={(e) => {
                  try {
                    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
                  } catch (_) {}
                  stopVoiceInputAndSend();
                }}
                onContextMenu={(e) => e.preventDefault()}
                className={`select-none touch-none p-2.5 sm:p-3 rounded-xl sm:rounded-2xl border transition-all flex items-center justify-center shrink-0 cursor-pointer ${
                  isRecording
                    ? 'bg-rose-600 border-rose-700 text-white animate-pulse shadow-lg shadow-rose-500/30 scale-105 ring-2 ring-rose-400'
                    : 'bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200 active:scale-95'
                }`}
                title={isRecording ? '離すと送信されます' : '押しっぱなしで話す（指を離すと自動送信）'}
              >
                <span className="text-base sm:text-lg">{isRecording ? '🔴' : '🎙️'}</span>
              </button>

              {(() => {
                const recentAiMsg = [...messages].reverse().find((m) => m.sender === 'ai' && m.assignedAgentLabel);
                const currentAgentLabel = recentAiMsg?.assignedAgentLabel ? recentAiMsg.assignedAgentLabel.replace(/AGENT \d+:\s*/, '') : '';
                const placeholderText = currentAgentLabel
                  ? `${currentAgentLabel} へ続けて質問を入力、またはマイク長押しで発話...`
                  : 'お困りごとを入力、またはマイク長押しで発話...';

                return (
                  <input
                    ref={inputRef}
                    type="text"
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    placeholder={placeholderText}
                    disabled={isLoading}
                    className="flex-1 min-w-0 bg-slate-50 border border-slate-300 rounded-xl sm:rounded-2xl px-3 sm:px-4 py-2.5 sm:py-3 text-xs sm:text-base text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 focus:bg-white transition-all shadow-inner"
                  />
                );
              })()}

              <button
                type="submit"
                disabled={(!inputText.trim() && !capturedImage) || isLoading}
                className="px-3.5 sm:px-5 py-2.5 sm:py-3 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-xl sm:rounded-2xl shadow-md shadow-blue-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-all text-xs sm:text-base shrink-0"
              >
                送信
              </button>
            </form>
          </div>
        </section>

        {/* 右ペイン: 診断カルテ */}
        {isKarteOpen && (
          <section
            className="relative w-full md:w-[var(--karte-w)] flex-none flex flex-col h-full bg-white border-l border-slate-200 shadow-sm animate-in fade-in duration-200 z-10 min-w-0 max-w-full"
            style={{ '--karte-w': `${karteWidth}px` } as React.CSSProperties}
          >
            {/* 幅変更ハンドル（PC 幅のみ）: ドラッグ / ←→ キー / ダブルクリックで初期幅 */}
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="カルテの幅を変更"
              aria-valuemin={KARTE_MIN_WIDTH}
              aria-valuemax={KARTE_MAX_WIDTH}
              aria-valuenow={karteWidth}
              tabIndex={0}
              title="ドラッグで幅を変更（ダブルクリックで元の幅）"
              onPointerDown={handleKarteResizeStart}
              onPointerMove={handleKarteResizeMove}
              onPointerUp={handleKarteResizeEnd}
              onPointerCancel={handleKarteResizeEnd}
              onDoubleClick={() => updateKarteWidth(KARTE_DEFAULT_WIDTH, true)}
              onKeyDown={handleKarteResizeKey}
              className="hidden md:block absolute left-0 top-0 h-full w-1.5 -translate-x-1/2 cursor-col-resize touch-none z-20 bg-transparent hover:bg-blue-400/60 focus:bg-blue-500/60 focus:outline-none transition-colors"
            />
            <div className="flex-none border-b border-slate-200 px-3 sm:px-4 py-2 sm:py-3 bg-slate-50/80 flex items-center justify-between gap-1.5">
              <div className="flex items-center space-x-1.5 min-w-0">
                <span className="text-base sm:text-lg shrink-0">📋</span>
                <h2 className="text-xs sm:text-sm font-bold text-slate-900 truncate">
                  AI診断カルテ
                </h2>
              </div>
              <div className="flex items-center space-x-1 sm:space-x-1.5 shrink-0">
                <div className="flex items-center space-x-0.5 sm:space-x-1 bg-slate-200/70 p-0.5 sm:p-1 rounded-xl">
                  <button
                    onClick={() => setActiveTab('reasoning')}
                    className={`text-[11px] sm:text-xs px-2 sm:px-2.5 py-1 font-bold rounded-lg transition-all ${
                      activeTab !== 'knowledge'
                        ? 'bg-white text-blue-600 shadow-sm'
                        : 'text-slate-600 hover:text-slate-900'
                    }`}
                  >
                    調査手順
                  </button>
                  <button
                    onClick={() => setActiveTab('knowledge')}
                    className={`text-[11px] sm:text-xs px-2 sm:px-2.5 py-1 font-bold rounded-lg transition-all ${
                      activeTab === 'knowledge'
                        ? 'bg-white text-blue-600 shadow-sm'
                        : 'text-slate-600 hover:text-slate-900'
                    }`}
                  >
                    事例集
                  </button>
                </div>
                {/* 閉じるボタン */}
                <button
                  onClick={() => setIsKarteOpen(false)}
                  className="p-1 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-200 transition-colors text-xs font-bold w-6 h-6 sm:w-7 sm:h-7 flex items-center justify-center border border-slate-200 shrink-0"
                  title="カルテを閉じる"
                >
                  ✕
                </button>
              </div>
            </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-slate-50/50">
            {activeTab === 'knowledge' ? (
              <div className="space-y-4">
                {/* 検索バーとカテゴリフィルター */}
                <div className="bg-white p-3.5 rounded-xl border border-slate-200 shadow-sm space-y-3">
                  <div className="relative">
                    <span className="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none text-slate-400 text-sm">
                      🔍
                    </span>
                    <input
                      type="text"
                      value={knowledgeSearch}
                      onChange={(e) => setKnowledgeSearch(e.target.value)}
                      placeholder="エラーコード、店舗名、事象で絞り込み（例: ERR-GATE, 中央店, ゲート）"
                      className="w-full pl-9 pr-8 py-2 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:bg-white text-slate-800"
                    />
                    {knowledgeSearch && (
                      <button
                        onClick={() => setKnowledgeSearch('')}
                        className="absolute inset-y-0 right-0 flex items-center pr-3 text-slate-400 hover:text-slate-600 text-xs font-bold"
                      >
                        ✕
                      </button>
                    )}
                  </div>

                  {/* カテゴリ選択タブ */}
                  <div className="flex flex-wrap gap-1.5 text-xs">
                    {[
                      { id: 'all', label: 'すべて' },
                      { id: 'pos', label: '🛒 スマートカート・電子棚札・POS' },
                      { id: 'network', label: '📶 店舗Wi-Fi・NW' },
                      { id: 'order', label: '📦 AI自動発注・EDI' },
                      { id: 'infra', label: '🖥️ AIカメラ・基幹基盤' },
                    ].map((cat) => (
                      <button
                        key={cat.id}
                        onClick={() => setKnowledgeCategory(cat.id)}
                        className={`px-2.5 py-1 rounded-lg font-medium transition-all ${
                          knowledgeCategory === cat.id
                            ? 'bg-blue-600 text-white font-bold shadow-xs'
                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                        }`}
                      >
                        {cat.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* 事例カード一覧 */}
                <div className="space-y-3">
                  {knowledgeData.tickets
                    .filter((t) => {
                      const matchCategory =
                        knowledgeCategory === 'all' || t.domain === knowledgeCategory;
                      const q = knowledgeSearch.toLowerCase();
                      const matchSearch =
                        !q ||
                        t.title.toLowerCase().includes(q) ||
                        t.store_or_system.toLowerCase().includes(q) ||
                        t.symptom.toLowerCase().includes(q) ||
                        t.resolution.toLowerCase().includes(q) ||
                        t.ticket_id.toLowerCase().includes(q);
                      return matchCategory && matchSearch;
                    })
                    .map((t) => {
                      const domainBadges: Record<string, { label: string; color: string }> = {
                        pos: { label: 'スマートカート・電子棚札・POS', color: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
                        network: { label: '店舗Wi-Fi・NW', color: 'bg-purple-50 text-purple-700 border-purple-200' },
                        order: { label: 'AI自動発注・EDI', color: 'bg-amber-50 text-amber-700 border-amber-200' },
                        infra: { label: 'AIカメラ・基幹基盤', color: 'bg-blue-50 text-blue-700 border-blue-200' },
                      };
                      const badge = domainBadges[t.domain] || { label: t.domain, color: 'bg-slate-100 text-slate-700 border-slate-200' };

                      return (
                        <div
                          key={t.ticket_id}
                          className="bg-white rounded-xl p-3.5 sm:p-4 border border-slate-200 shadow-sm space-y-3 transition-all hover:border-blue-300 break-words min-w-0"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-1 border-b border-slate-100 pb-2">
                            <div className="flex items-center space-x-2">
                              <span className="font-mono font-bold text-blue-700 bg-blue-50 px-2 py-0.5 rounded text-xs border border-blue-100">
                                {t.ticket_id}
                              </span>
                              <span className={`text-[11px] font-bold px-2 py-0.5 rounded border ${badge.color}`}>
                                {badge.label}
                              </span>
                            </div>
                            <div className="flex items-center space-x-2 text-[11px] text-slate-500">
                              <span>⏱️ 解決: 約{t.resolved_in_minutes || 15}分</span>
                            </div>
                          </div>

                          <div className="flex items-center text-xs font-semibold text-slate-700 bg-slate-50 px-2.5 py-1.5 rounded-lg border border-slate-100">
                            <span className="mr-1.5 text-blue-600">📍</span>
                            <span>{t.store_or_system}</span>
                          </div>

                          <h4 className="font-bold text-slate-900 text-sm leading-snug">
                            {t.title}
                          </h4>

                          <div className="bg-amber-50/70 p-2.5 rounded-lg border border-amber-200/60 text-xs text-amber-950 space-y-0.5">
                            <div className="font-bold text-amber-900 flex items-center">
                              <span className="mr-1">🚨</span> 発生事象・エラー詳細:
                            </div>
                            <div className="leading-relaxed pl-4">{t.symptom}</div>
                          </div>

                          <div className="text-xs text-slate-700 pl-1 space-y-0.5">
                            <span className="font-bold text-slate-900 flex items-center">
                              <span className="mr-1">🔍</span> 判明した原因:
                            </span>
                            <p className="leading-relaxed pl-4 text-slate-600">{t.root_cause}</p>
                          </div>

                          <div className="bg-emerald-50 p-2.5 rounded-lg border border-emerald-200 text-xs text-emerald-950 space-y-0.5">
                            <div className="font-bold text-emerald-800 flex items-center">
                              <span className="mr-1">✅</span> 実施した解決手順:
                            </div>
                            <div className="leading-relaxed pl-4 font-medium">{t.resolution}</div>
                          </div>

                          <div className="pt-1 flex justify-end">
                            <button
                              onClick={() => {
                                let sampleQuery = "";
                                if (t.ticket_id === "#12345") {
                                  sampleQuery = "中央店のスマートカート専用ゲートで、専用ゲート通過認証エラー（ERR-GATE-408）が発生し通過できません。過去の類似事例と対処法を教えてください。";
                                } else if (t.ticket_id === "#12110") {
                                  sampleQuery = "駅前店の惣菜売場で、電子棚札（ESL）の夕方AI値下げ価格が反映されません（ESL-SYNC-501）。復旧手順は？";
                                } else if (t.ticket_id === "#12085") {
                                  sampleQuery = "東店で、スマートカートのスキャン漏れ検知エラー（ERR-CART-WGT-105）が頻発しています。対処手順を教えてください。";
                                } else if (t.domain === 'network') {
                                  sampleQuery = "西店のバックヤードでネットワークが繋がらない状態です。検品ハンディもオフラインになっています。";
                                } else {
                                  sampleQuery = `${t.store_or_system}で「${t.title}」が発生しました。対処手順を教えてください。`;
                                }
                                setInputText(sampleQuery);
                                setActiveTab('chat');
                              }}
                              className="text-xs font-bold text-blue-600 hover:text-blue-800 bg-blue-50 hover:bg-blue-100 px-3 py-1.5 rounded-lg border border-blue-200 transition-colors flex items-center"
                            >
                              <span className="mr-1">💬</span> この事例をAIに相談する
                            </button>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>
            ) : isLoading ? (
              /* リアルタイム調査プロセス進行アニメーション */
              <div className="space-y-3">
                {liveReasoningSteps.length > 0 && (
                  <div className="bg-indigo-50 border border-indigo-200 rounded-xl p-3 text-xs text-indigo-950 space-y-1.5 shadow-xs">
                    <div className="font-bold flex items-center gap-1.5 text-indigo-900">
                      <span>⚡</span>
                      <span>Gemini Live リアルタイム推論ステップ ({liveReasoningSteps.length}):</span>
                    </div>
                    <div className="space-y-1">
                      {liveReasoningSteps.map((step, idx) => (
                        <div key={idx} className="flex items-center gap-2 font-mono text-[11px] text-indigo-800 bg-white/80 px-2 py-1 rounded border border-indigo-100">
                          <span className="w-1.5 h-1.5 rounded-full bg-indigo-500"></span>
                          <span>{step.title}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div className="bg-blue-50 border border-blue-200 rounded-xl p-3.5 text-xs text-blue-900 flex items-center justify-between shadow-xs">
                  <div className="flex items-center space-x-2 font-bold">
                    <span className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></span>
                    <span>🤖 AIが現場の困りごとを調査・推論中...</span>
                  </div>
                  <span className="text-[11px] font-semibold text-blue-600 bg-white px-2.5 py-0.5 rounded-full border border-blue-200 shadow-2xs">
                    調査フェーズ {loadingPhase}/3
                  </span>
                </div>

                <div className="space-y-3">
                  <div className={`p-4 rounded-xl border transition-all ${loadingPhase >= 1 ? 'bg-white border-blue-300 shadow-sm' : 'bg-slate-50 border-slate-200 opacity-50'}`}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2 font-bold text-sm text-slate-800">
                        <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs flex items-center justify-center font-black">
                          1
                        </span>
                        <span>{loadingPhase > 1 ? '✅' : '🔍'} 意図判定・対象設備の特定</span>
                      </div>
                      {loadingPhase === 1 && (
                        <span className="text-[10px] font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded animate-pulse">進行中...</span>
                      )}
                    </div>
                    <p className="text-xs text-slate-600 pl-8 mt-1.5 leading-relaxed">
                      {loadingPhase >= 1 ? '問い合わせ文から障害キーワード（スマートカート/専用ゲート/ESL等）を自然言語解析しています...' : '待機中'}
                    </p>
                  </div>

                  <div className={`p-4 rounded-xl border transition-all ${loadingPhase >= 2 ? 'bg-white border-blue-300 shadow-sm' : 'bg-slate-50 border-slate-200 opacity-50'}`}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2 font-bold text-sm text-slate-800">
                        <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs flex items-center justify-center font-black">
                          2
                        </span>
                        <span>{loadingPhase > 2 ? '✅' : loadingPhase === 2 ? '🔄' : '⏳'} 過去インシデント事例のベクトル照合</span>
                      </div>
                      {loadingPhase === 2 && (
                        <span className="text-[10px] font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded animate-pulse">進行中...</span>
                      )}
                    </div>
                    <p className="text-xs text-slate-600 pl-8 mt-1.5 leading-relaxed">
                      {loadingPhase >= 2 ? 'BigQuery 上の過去トラブル解決カルテから類似事例（類似度90%以上）を照合しています...' : '待機中'}
                    </p>
                  </div>

                  <div className={`p-4 rounded-xl border transition-all ${loadingPhase >= 3 ? 'bg-white border-blue-300 shadow-sm' : 'bg-slate-50 border-slate-200 opacity-50'}`}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2 font-bold text-sm text-slate-800">
                        <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs flex items-center justify-center font-black">
                          3
                        </span>
                        <span>{loadingPhase === 3 ? '🔄' : '⏳'} Splunk 異常ログ照合 ＆ 一次復旧Runbook策定</span>
                      </div>
                      {loadingPhase === 3 && (
                        <span className="text-[10px] font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded animate-pulse">進行中...</span>
                      )}
                    </div>
                    <p className="text-xs text-slate-600 pl-8 mt-1.5 leading-relaxed">
                      {loadingPhase >= 3 ? '最新のエラーログと一次切り分け手順書を照合し、解決手順カルテをまとめています...' : '待機中'}
                    </p>
                  </div>
                </div>
              </div>
            ) : currentReasoning && currentReasoning.length > 0 ? (
              <div className="space-y-3">
                <div className="bg-blue-50 border border-blue-200 rounded-xl p-3 text-xs text-blue-900">
                  <span className="font-bold">💡 AIの調査プロセス:</span> 
                  現場の質問を受け、AIがどの情報を調べて結論を出したかをステップ順に記録しています。
                </div>

                {currentReasoning.map((step, idx) => {
                  const icons = ['🗣️', '👤', '📚', '🩺', '📋', '🤝'];
                  const icon = icons[idx] || '✔️';

                  return (
                    <div
                      key={step.step_no}
                      className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm space-y-2 transition-all hover:border-blue-300"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-slate-900 text-sm flex items-center">
                          <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs flex items-center justify-center font-black mr-2">
                            {step.step_no}
                          </span>
                          <span className="mr-1.5">{icon}</span>
                          {step.title}
                        </span>
                        <span className="text-[11px] font-semibold text-slate-700 bg-slate-100 px-2 py-0.5 rounded-full flex items-center gap-1 border border-slate-200">
                          <img
                            src={getAgentAvatar(step.agent).icon}
                            alt=""
                            className="w-3.5 h-3.5 rounded-full object-cover"
                          />
                          <span>{getAgentAvatar(step.agent).shortName}</span>
                        </span>
                      </div>

                      <p className="text-slate-700 text-xs leading-relaxed pl-8">
                        {step.detail}
                      </p>

                      {step.evidence && step.evidence.length > 0 && (
                        <div className="ml-8 mt-2 bg-slate-50 rounded-lg p-2.5 border border-slate-200 text-xs space-y-1 min-w-0 max-w-full overflow-hidden">
                          <div className="font-bold text-slate-600 text-[11px] flex items-center">
                            <span>📌 参照した証拠・データ:</span>
                          </div>
                          <div className="text-slate-700 text-xs font-mono bg-white p-2 rounded border border-slate-200 overflow-x-auto max-w-full break-all">
                            {Array.isArray(step.evidence) && step.evidence[0]?.title
                              ? `タイトル: ${step.evidence[0].title}\n番号: ${step.evidence[0].ticket_id || step.evidence[0].runbook_id || 'N/A'}`
                              : JSON.stringify(step.evidence, null, 2)}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-center p-6 text-slate-400">
                <span className="text-4xl mb-3">🩺</span>
                <p className="text-sm font-bold text-slate-700">診断カルテがここに表示されます</p>
                <p className="text-xs text-slate-500 mt-1 max-w-xs">
                  左のチャットまたは上のワンクリックボタンでお困りごとを送信すると、AIが調査したステップがここにリアルタイムで表示されます。
                </p>
              </div>
            )}
          </div>
        </section>
      )}
      </main>

      {/* カメラ撮影モーダル */}
      {isCameraModalOpen && (
        <div className="fixed inset-0 bg-slate-900/80 backdrop-blur-xs flex items-center justify-center z-50 p-3 sm:p-4 animate-in fade-in duration-150">
          <div className="bg-white rounded-2xl sm:rounded-3xl max-w-lg w-full max-h-[90dvh] overflow-hidden shadow-2xl border border-slate-200 flex flex-col">
            <div className="flex items-center justify-between px-4 sm:px-5 py-3 sm:py-4 border-b border-slate-200 bg-slate-50/80">
              <div className="flex items-center space-x-2">
                <span className="text-lg sm:text-xl">📷</span>
                <div>
                  <h3 className="text-xs sm:text-sm font-bold text-slate-900">現場機器・画面のカメラ撮影</h3>
                  <p className="text-[10px] sm:text-[11px] text-slate-500">ランプ状態やエラー画面を枠内に収めて撮影してください</p>
                </div>
              </div>
              <button
                type="button"
                onClick={stopCamera}
                className="p-1.5 rounded-xl text-slate-400 hover:text-slate-700 hover:bg-slate-200 transition-colors text-xs font-bold"
                title="閉じる"
              >
                ✕
              </button>
            </div>

            <div className="relative bg-black aspect-4/3 flex items-center justify-center overflow-hidden">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="w-full h-full object-cover"
              />
              <div className="absolute inset-4 sm:inset-6 border-2 border-white/50 border-dashed rounded-2xl pointer-events-none flex items-center justify-center">
                <span className="text-white/70 text-[11px] sm:text-xs bg-black/50 px-3 py-1 rounded-full backdrop-blur-xs">
                  ここに機器または画面を合わせてください
                </span>
              </div>
            </div>

            <div className="p-3 sm:p-4 bg-slate-50 flex items-center justify-between gap-2 border-t border-slate-200">
              <button
                type="button"
                onClick={() => {
                  stopCamera();
                  if (fileInputRef.current) fileInputRef.current.click();
                }}
                className="px-3 sm:px-3.5 py-2 rounded-xl text-xs font-bold text-slate-600 bg-white border border-slate-300 hover:bg-slate-100 transition-all flex items-center space-x-1"
              >
                <span>📁</span>
                <span className="hidden sm:inline">端末の写真を選ぶ</span>
                <span className="sm:hidden">写真選択</span>
              </button>

              <button
                type="button"
                onClick={capturePhoto}
                className="px-4 sm:px-6 py-2 sm:py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-full font-bold text-xs sm:text-sm shadow-lg shadow-blue-500/30 flex items-center space-x-1.5 active:scale-95 transition-all"
              >
                <span className="text-sm sm:text-base">📸</span>
                <span>撮影する</span>
              </button>

              <button
                type="button"
                onClick={stopCamera}
                className="px-3 sm:px-3.5 py-2 rounded-xl text-xs font-bold text-slate-500 hover:text-slate-800 transition-colors"
              >
                閉じる
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

