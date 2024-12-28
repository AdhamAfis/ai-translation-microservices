"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { callAPI } from "@/lib/api";
import React, { useState, useEffect } from "react";
import { toast } from "sonner";
import { TranslationHistory } from "@/components/history/TranslationHistory";

type SummaryResponse = {
  id: string;
  status: string;
  result: {
    summary: string;
    style: string;
    bullet_points: boolean;
    length: number;
    truncated: boolean;
  };
  cache_hit: boolean;
};

const styles = [
  { value: "formal", label: "Formal" },
  { value: "informal", label: "Informal" },
  { value: "technical", label: "Technical" },
  { value: "executive", label: "Executive" },
  { value: "creative", label: "Creative" }
];

const TextSummarizerPage: React.FC = () => {
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [style, setStyle] = useState("formal");
  const [maxLength, setMaxLength] = useState(500);
  const [bulletPoints, setBulletPoints] = useState(false);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    fetchHistory();
  }, []);

  const fetchHistory = async () => {
    const response = await callAPI("/history/user123?type=summarization");
    if (response.data) {
      setHistory(response.data.data);
    }
  };

  const handleSummarize = async () => {
    setIsLoading(true);
    const response = await callAPI<SummaryResponse>("/summarization", {
      method: "POST",
      body: JSON.stringify({
        user_id: "user123",
        chat_id: Date.now().toString(),
        text: input,
        style: style,
        max_length: maxLength,
        bullet_points: bulletPoints
      })
    });

    if (response.error) {
      toast.error(response.error.detail);
    } else if (response.data) {
      setOutput(response.data.result.summary);
      fetchHistory(); // Refresh history after new summarization
    }
    
    setIsLoading(false);
  };

  return (
    <div className="container mx-auto p-4 max-w-4xl">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-bold">Text Summarizer</h1>
      </div>
      
      <div className="grid gap-6">
        <Card>
          <CardHeader className="text-lg font-semibold">Input Text</CardHeader>
          <CardContent>
            <Textarea 
              placeholder="Enter the text you want to summarize..."
              className="min-h-[200px]"
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="text-lg font-semibold">Summary Options</CardHeader>
          <CardContent className="grid gap-6">
            <div className="grid gap-2">
              <Label>Style</Label>
              <Select value={style} onValueChange={setStyle}>
                <SelectTrigger>
                  <SelectValue placeholder="Select style" />
                </SelectTrigger>
                <SelectContent>
                  {styles.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-2">
              <Label>Maximum Length ({maxLength} characters)</Label>
              <Slider
                value={[maxLength]}
                onValueChange={(value) => setMaxLength(value[0])}
                min={100}
                max={1000}
                step={50}
              />
            </div>

            <div className="flex items-center justify-between">
              <Label>Use Bullet Points</Label>
              <Switch
                checked={bulletPoints}
                onCheckedChange={setBulletPoints}
              />
            </div>
          </CardContent>
        </Card>

        <Button 
          onClick={handleSummarize} 
          disabled={!input || isLoading}
          className="w-full"
        >
          {isLoading ? "Summarizing..." : "Summarize"}
        </Button>

        <Card>
          <CardHeader className="text-lg font-semibold">Summary</CardHeader>
          <CardContent>
            <Textarea 
              readOnly 
              value={output}
              placeholder="Summary will appear here..."
              className="min-h-[150px]"
            />
          </CardContent>
        </Card>
      </div>
      <TranslationHistory 
        history={history} 
        title="Summarization History" 
      />
    </div>
  );
};

export default TextSummarizerPage;
