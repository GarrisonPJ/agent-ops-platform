import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

export interface PlaybackState {
  currentStepIndex: number;
  isPlaying: boolean;
  playbackSpeed: 0.5 | 1 | 2 | 4;
}

const initialState: PlaybackState = {
  currentStepIndex: 0,
  isPlaying: false,
  playbackSpeed: 1,
};

const trajectorySlice = createSlice({
  name: "trajectory",
  initialState,
  reducers: {
    setCurrentStep(state, action: PayloadAction<number>) {
      state.currentStepIndex = Math.max(0, action.payload);
    },
    togglePlay(state) {
      state.isPlaying = !state.isPlaying;
    },
    setSpeed(state, action: PayloadAction<0.5 | 1 | 2 | 4>) {
      state.playbackSpeed = action.payload;
    },
    resetPlayback() {
      return initialState;
    },
  },
});

export const {
  setCurrentStep,
  togglePlay,
  setSpeed,
  resetPlayback,
} = trajectorySlice.actions;
export default trajectorySlice.reducer;
