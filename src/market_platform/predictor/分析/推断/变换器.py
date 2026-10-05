"""
Transformer 时序模型 — 支持单尺度 / 融合多尺度 + regime 模式
"""

from market_platform.predictor.核心.路径.项目 import checkpoints_dir
from market_platform.predictor.配置 import get_config
from market_platform.predictor.分析.特征.状态路由 import classify_regime, REGIME_BULL, REGIME_BEAR, REGIME_RANGE

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from loguru import logger
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
import pandas as pd
from sklearn.preprocessing import StandardScaler


# ═══════════════════════════════════════════
# 单尺度 Transformer
# ═══════════════════════════════════════════

class TimeSeriesTransformer(nn.Module):
    def __init__(self, n_features, seq_len=20, d_model=128, n_heads=4, n_layers=3, dropout=0.1, n_classes=3):
        super().__init__()
        self.input_proj = nn.Linear(n_features, d_model)
        self.pos_encoding = nn.Parameter(torch.randn(1, seq_len, d_model) * 0.02)
        el = nn.TransformerEncoderLayer(d_model, nhead=n_heads, dim_feedforward=d_model*4, dropout=dropout, batch_first=True)
        self.encoder = nn.TransformerEncoder(el, num_layers=n_layers)
        self.classifier = nn.Sequential(nn.Linear(d_model, 64), nn.ReLU(), nn.Dropout(dropout), nn.Linear(64, n_classes))
    def forward(self, x):
        x = self.input_proj(x) + self.pos_encoding
        x = self.encoder(x)
        x = x[:, -1, :]
        x = self.classifier(x)
        return F.log_softmax(x, dim=1)


# ═══════════════════════════════════════════
# 融合多尺度 Transformer（20日 + 所有特征 + regime）
# ═══════════════════════════════════════════

class MultiScaleTransformer(nn.Module):
    """
    每步特征 = 短期(8) + 中期(11) + 长期(11) = 30 维
    20 日序列 → Transformer → 注意力融合多尺度
    regime 作为全局偏置嵌入，加到输出上
    """
    def __init__(self, n_features, seq_len=20, d_model=96, n_heads=4, n_layers=3, dropout=0.1, n_classes=3, use_regime=True):
        super().__init__()
        self.use_regime = use_regime
        self.input_proj = nn.Linear(n_features, d_model)
        self.pos_encoding = nn.Parameter(torch.randn(1, seq_len, d_model) * 0.02)
        el = nn.TransformerEncoderLayer(d_model, nhead=n_heads, dim_feedforward=d_model*4, dropout=dropout, batch_first=True)
        self.encoder = nn.TransformerEncoder(el, num_layers=n_layers)
        if use_regime:
            self.regime_embed = nn.Linear(3, d_model)
        cin = d_model * 2 if use_regime else d_model
        self.classifier = nn.Sequential(
            nn.Linear(cin, 96), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(96, 64), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(64, n_classes),
        )
    def forward(self, x, regime=None):
        x = self.input_proj(x) + self.pos_encoding
        x = self.encoder(x)
        last = x[:, -1, :]
        pooled = x.mean(dim=1)
        if regime is not None and self.use_regime:
            r = self.regime_embed(regime)
            x = torch.cat([last + r, pooled], dim=-1)
        else:
            x = torch.cat([last, pooled], dim=-1)
        x = self.classifier(x)
        return F.log_softmax(x, dim=1)


_EXCLUDE_COLS = {
    "date", "label_direction", "label_return",
    "label_week_direction", "label_week_return",
    "label_soft_dn", "label_soft_flat", "label_soft_up",
    "open", "high", "low", "close", "volume", "amount", "turnover", "index_code",
}


# ═══════════════════════════════════════════
# 统一预测器
# ═══════════════════════════════════════════

class TransformerPredictor:
    def __init__(self, config_override: Optional[dict] = None):
        cfg = dict(get_config()["model"]["transformer"])
        if config_override:
            cfg.update(config_override)
        self.cfg = cfg
        self.mode = str(cfg.get("mode", "single"))
        self.model = None
        self.feature_cols: Optional[List[str]] = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._is_trained = False
        self.scaler = StandardScaler()
        self.scaler_fitted = False
        logger.info(f"🔧 Transformer 使用设备: {self.device}  模式: {self.mode}")

    def _resolve_feature_cols(self, df):
        if self.mode == "multiscale":
            cols = [c for c in (self.cfg.get("feature_cols_all") or []) if c in df.columns]
            missing = [c for c in (self.cfg.get("feature_cols_all") or []) if c not in df.columns]
            if missing:
                logger.warning(f"⚠️ 缺失特征: {missing}")
            return {"all": cols}
        cols = [c for c in (self.cfg.get("feature_cols") or []) if c in df.columns]
        if not cols:
            cols = [c for c in df.select_dtypes(include=[np.number]).columns if c not in _EXCLUDE_COLS and not str(c).startswith("tf_") and not str(c).startswith("label_")]
        return {"single": cols}

    def _compute_regime(self, row):
        r = classify_regime(row)
        return (1 if r == REGIME_BULL else 0, 1 if r == REGIME_BEAR else 0, 1 if r == REGIME_RANGE else 0)

    def _resolve_train_labels(self, df: pd.DataFrame):
        """软标签仅在三列齐全且有限时使用；缺失不填均匀分布（会塌缩到先验）。"""
        hard = self._dir_to_cls(df["label_direction"].values)
        soft_cols = ("label_soft_dn", "label_soft_flat", "label_soft_up")
        if not all(c in df.columns for c in soft_cols):
            return hard, False
        soft = df.loc[:, list(soft_cols)].to_numpy(dtype=np.float64)
        valid = np.isfinite(soft).all(axis=1) & (soft.sum(axis=1) > 0.5)
        if not bool(valid.any()):
            return hard, False
        # 无效行回退 hard one-hot，整体仍走 soft/KL 路径
        onehot = np.eye(3, dtype=np.float32)[hard]
        soft_f = soft.astype(np.float32)
        soft_f[~valid] = onehot[~valid]
        # 行归一
        soft_f = soft_f / np.clip(soft_f.sum(axis=1, keepdims=True), 1e-8, None)
        n_soft = int(valid.sum())
        logger.info(f"🏷️ 标签: soft有效 {n_soft}/{len(df)}，其余 hard one-hot")
        return soft_f, True

    def _build_sequences(self, df, feature_cols, seq_len):
        data = df[feature_cols].fillna(0).values
        labels, use_soft = self._resolve_train_labels(df)
        X, y = [], []
        for i in range(seq_len, len(data)):
            X.append(data[i - seq_len : i])
            y.append(labels[i])
        return (
            np.array(X, dtype=np.float32),
            np.array(y, dtype=np.float32 if use_soft else np.int64),
        )

    def _build_multiscale_data(self, df, feat_map):
        base_cols = feat_map["all"]
        seq_len = int(self.cfg.get("seq_len", 20))
        use_reg = self.cfg.get("regime_enabled", True)
        labels, use_soft = self._resolve_train_labels(df)
        Xs, Rs, ys_out = [], [], []
        for i in range(seq_len, len(df)):
            Xs.append(df[base_cols].iloc[i - seq_len : i].fillna(0).values.astype(np.float32))
            if use_reg:
                Rs.append(np.array(self._compute_regime(df.iloc[i - 1]), dtype=np.float32))
            ys_out.append(labels[i])
        X = np.array(Xs, dtype=np.float32)
        R = np.array(Rs, dtype=np.float32) if use_reg else None
        y = np.array(ys_out, dtype=np.float32 if use_soft else np.int64)
        logger.info(f"📊 多尺度: X={X.shape} regime={'Y' if use_reg else 'N'} y={y.shape}")
        return X, R, y

    @staticmethod
    def _dir_to_cls(y):
        m = {-1: 0, 0: 1, 1: 2}
        return np.array([m.get(int(v), 1) for v in y], dtype=np.int64)

    @staticmethod
    def _cls_to_dir(y):
        m = {0: -1, 1: 0, 2: 1}
        return np.array([m.get(int(v), 0) for v in y], dtype=np.int64)

    # ── 训练 ──

    def train(self, df, val_df=None):
        feat_map = self._resolve_feature_cols(df)
        if self.mode == "multiscale":
            return self._train_ms(df, val_df, feat_map)
        return self._train_single(df, val_df, feat_map)

    def _train_single(self, df, val_df, feat_map):
        fc = feat_map["single"]
        self.feature_cols = fc
        seq_len = int(self.cfg.get("seq_len", 20))
        X, y = self._build_sequences(df, fc, seq_len)
        if len(X) == 0:
            raise RuntimeError("无有效序列")
        X = self._scale(X, fit=True)
        if val_df is not None:
            Xv, yv = self._build_sequences(val_df, fc, seq_len)
            Xv = self._scale(Xv, fit=False)
        else:
            sp = int(len(X)*0.8)
            Xv, yv = X[sp:], y[sp:]
            X, y = X[:sp], y[:sp]
        use_soft = y.ndim == 2
        self.model = TimeSeriesTransformer(n_features=X.shape[2], seq_len=seq_len, d_model=int(self.cfg.get("d_model",128)), n_heads=int(self.cfg.get("n_heads",4)), n_layers=int(self.cfg.get("n_layers",3)), dropout=float(self.cfg.get("dropout",0.1)), n_classes=3).to(self.device)
        return self._fit_loop(
            DataLoader(TensorDataset(torch.tensor(X), torch.tensor(y)), batch_size=int(self.cfg.get("batch_size",64)), shuffle=True),
            DataLoader(TensorDataset(torch.tensor(Xv), torch.tensor(yv)), batch_size=int(self.cfg.get("batch_size",64))),
            use_soft,
            y_train=y,
        )

    def _train_ms(self, df, val_df, feat_map):
        X, R, y = self._build_multiscale_data(df, feat_map)
        X = self._scale(X, fit=True)
        if val_df is not None:
            Xv, Rv, yv = self._build_multiscale_data(val_df, feat_map)
            Xv = self._scale(Xv, fit=False)
        else:
            sp = int(len(X)*0.8)
            Xv, Rv, yv = X[sp:], R[sp:] if R is not None else None, y[sp:]
            X, R, y = X[:sp], R[:sp] if R is not None else None, y[:sp]
        use_soft = y.ndim == 2
        use_reg = R is not None
        self.model = MultiScaleTransformer(n_features=X.shape[2], seq_len=int(self.cfg.get("seq_len",20)), d_model=int(self.cfg.get("d_model",96)), n_heads=int(self.cfg.get("n_heads",4)), n_layers=int(self.cfg.get("n_layers",3)), dropout=float(self.cfg.get("dropout",0.1)), n_classes=3, use_regime=use_reg).to(self.device)
        if use_reg:
            tr = DataLoader(TensorDataset(torch.tensor(X), torch.tensor(R), torch.tensor(y)), batch_size=int(self.cfg.get("batch_size",64)), shuffle=True)
            va = DataLoader(TensorDataset(torch.tensor(Xv), torch.tensor(Rv), torch.tensor(yv)), batch_size=int(self.cfg.get("batch_size",64)))
        else:
            tr = DataLoader(TensorDataset(torch.tensor(X), torch.tensor(y)), batch_size=int(self.cfg.get("batch_size",64)), shuffle=True)
            va = DataLoader(TensorDataset(torch.tensor(Xv), torch.tensor(yv)), batch_size=int(self.cfg.get("batch_size",64)))
        return self._fit_loop(tr, va, use_soft, use_reg=use_reg, y_train=y)

    def _scale(self, X, fit=False):
        s = X.shape
        f = X.reshape(-1, s[-1])
        if fit:
            self.scaler.fit(f)
            self.scaler_fitted = True
        return self.scaler.transform(f).reshape(s).astype(np.float32)

    def _class_weights(self, y: np.ndarray) -> Optional[torch.Tensor]:
        """按训练集类别频次反比加权，缓解塌缩到多数类。"""
        if y.ndim == 2:
            cls = y.argmax(axis=1)
        else:
            cls = y.astype(np.int64)
        counts = np.bincount(cls, minlength=3).astype(np.float64)
        if counts.min() <= 0:
            return None
        w = counts.sum() / (3.0 * counts)
        w = w / w.mean()
        logger.info(f"⚖️ 类别权重: down={w[0]:.3f} flat={w[1]:.3f} up={w[2]:.3f}")
        return torch.tensor(w, dtype=torch.float32, device=self.device)

    def _fit_loop(self, loader, val_loader, use_soft, use_reg=False, y_train: Optional[np.ndarray] = None):
        opt = optim.AdamW(self.model.parameters(), lr=float(self.cfg.get("learning_rate", 0.001)))
        weights = self._class_weights(y_train) if y_train is not None else None
        if use_soft:
            criterion = nn.KLDivLoss(reduction="batchmean")
        else:
            criterion = nn.CrossEntropyLoss(weight=weights)
        epochs = int(self.cfg.get("epochs", 30))
        patience, best_val, wait = 10, float("inf"), 0
        best_state = None
        ta = va = 0.0
        ep = -1
        for ep in range(epochs):
            self.model.train()
            tl, tc, tt = 0.0, 0, 0
            for batch in loader:
                opt.zero_grad()
                if use_reg:
                    xb, rb, yb = [b.to(self.device) for b in batch]
                    out = self.model(xb, rb)
                else:
                    xb, yb = [b.to(self.device) for b in batch]
                    out = self.model(xb)
                if use_soft and weights is not None:
                    per = F.kl_div(out, yb, reduction="none").sum(dim=1)
                    w = (yb * weights).sum(dim=1)
                    loss = (per * w).mean()
                else:
                    loss = criterion(out, yb)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                opt.step()
                tl += loss.item()
                _, pd = out.max(1)
                gt = yb.max(1)[1] if use_soft else yb
                tc += (pd == gt).sum().item()
                tt += yb.size(0)
            self.model.eval()
            vl, vc, vt = 0.0, 0, 0
            with torch.no_grad():
                for batch in val_loader:
                    if use_reg:
                        xb, rb, yb = [b.to(self.device) for b in batch]
                        out = self.model(xb, rb)
                    else:
                        xb, yb = [b.to(self.device) for b in batch]
                        out = self.model(xb)
                    if use_soft and weights is not None:
                        per = F.kl_div(out, yb, reduction="none").sum(dim=1)
                        w = (yb * weights).sum(dim=1)
                        loss = (per * w).mean()
                    else:
                        loss = criterion(out, yb)
                    vl += loss.item()
                    _, pd = out.max(1)
                    gt = yb.max(1)[1] if use_soft else yb
                    vc += (pd == gt).sum().item()
                    vt += yb.size(0)
            ta = tc / max(tt, 1)
            va = vc / max(vt, 1)
            if (ep + 1) % 10 == 0 or ep == 0:
                logger.info(
                    f"Epoch {ep+1:3d}/{epochs} | Train L: {tl/len(loader):.4f} Acc: {ta:.4f} | "
                    f"Val L: {vl/len(val_loader):.4f} Acc: {va:.4f}"
                )
            if vl < best_val:
                best_val, wait = vl, 0
                best_state = {k: v.detach().cpu().clone() for k, v in self.model.state_dict().items()}
            else:
                wait += 1
                if wait >= patience:
                    logger.info(f"⏹️ Early stop at epoch {ep+1}")
                    break
        if best_state is not None:
            self.model.load_state_dict(best_state)
            self.model.to(self.device)
        self._is_trained = True
        return {"val_acc": round(va, 4), "best_val_loss": round(float(best_val), 4), "epochs": ep + 1}

    # ── 推理 ──

    def predict_proba_aligned(self, df):
        assert self._is_trained and self.model is not None, "未训练！"
        n = len(df)

        if self.mode == "multiscale":
            feat_map = self._resolve_feature_cols(df)
            base_cols = feat_map["all"]
            seq_len = int(self.cfg.get("seq_len",20))
            use_reg = self.cfg.get("regime_enabled", True)
            Xs, Rs = [], []
            for i in range(seq_len, n):
                Xs.append(df[base_cols].iloc[i-seq_len:i].fillna(0).values.astype(np.float32))
                if use_reg:
                    Rs.append(np.array(self._compute_regime(df.iloc[i-1]), dtype=np.float32))
            if not Xs:
                return np.full((n,3), np.nan, dtype=np.float32), np.zeros(n, dtype=np.int64)
            Xa = self._scale(np.array(Xs), fit=False)
            Ra = np.array(Rs) if use_reg else None
            self.model.eval()
            with torch.no_grad():
                if use_reg:
                    out = self.model(torch.tensor(Xa).to(self.device), torch.tensor(Ra).to(self.device))
                else:
                    out = self.model(torch.tensor(Xa).to(self.device))
            prob = torch.exp(out).cpu().numpy()
            cls = prob.argmax(axis=1)
            pf = np.full((n,3), np.nan, dtype=np.float32); pr = np.zeros(n, dtype=np.int64)
            pf[seq_len:] = prob; pr[seq_len:] = self._cls_to_dir(cls)
            return pf, pr

        # 单尺度
        feat_map = self._resolve_feature_cols(df)
        fc = feat_map["single"]
        seq_len = int(self.cfg.get("seq_len",20))
        data = df[fc].fillna(0).values
        rows = [data[i-seq_len:i] for i in range(seq_len, n)]
        if not rows:
            return np.full((n,3), np.nan, dtype=np.float32), np.zeros(n, dtype=np.int64)
        Xa = self._scale(np.array(rows), fit=False)
        self.model.eval()
        with torch.no_grad():
            out = self.model(torch.tensor(Xa).to(self.device))
        prob = torch.exp(out).cpu().numpy()
        cls = prob.argmax(axis=1)
        pf = np.full((n,3), np.nan, dtype=np.float32); pr = np.zeros(n, dtype=np.int64)
        pf[seq_len:] = prob; pr[seq_len:] = self._cls_to_dir(cls)
        return pf, pr

    def predict(self, df):
        _, pred = self.predict_proba_aligned(df)
        seq_len = int(self.cfg.get("seq_len",20))
        return pred[seq_len:]

    # ── 保存/加载 ──

    def save(self, name="transformer_model"):
        p = checkpoints_dir() / f"{name}.pt"
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = {"mode": self.mode}
        if self.mode == "multiscale" and hasattr(self.model, "input_proj"):
            meta["n_features"] = self.model.input_proj.in_features
        elif hasattr(self.model, "input_proj"):
            meta["n_features"] = self.model.input_proj.in_features
        sd = None
        if self.scaler_fitted:
            sd = {"mean": self.scaler.mean_.tolist(), "scale": self.scaler.scale_.tolist()}
        torch.save({"model_state_dict": self.model.state_dict(), "cfg": self.cfg, "meta": meta, "scaler": sd}, p)
        logger.info(f"💾 已保存: {p}")

    def try_load(self, name="transformer_model"):
        p = checkpoints_dir() / f"{name}.pt"
        if not p.exists():
            return False
        ck = torch.load(p, map_location=self.device, weights_only=False)
        self.cfg = ck.get("cfg", self.cfg)
        meta = ck.get("meta", {})
        self.mode = str(meta.get("mode", "single"))
        nf = int(meta.get("n_features", 30))
        if self.mode == "multiscale":
            d = int(self.cfg.get("d_model", 96))
            self.model = MultiScaleTransformer(n_features=nf, seq_len=int(self.cfg.get("seq_len",20)), d_model=d, n_heads=int(self.cfg.get("n_heads",4)), n_layers=int(self.cfg.get("n_layers",3)), dropout=float(self.cfg.get("dropout",0.1)), n_classes=3, use_regime=self.cfg.get("regime_enabled",True)).to(self.device)
        else:
            d = int(self.cfg.get("d_model", 128))
            self.model = TimeSeriesTransformer(n_features=nf, seq_len=int(self.cfg.get("seq_len",20)), d_model=d, n_heads=int(self.cfg.get("n_heads",4)), n_layers=int(self.cfg.get("n_layers",3)), dropout=float(self.cfg.get("dropout",0.1)), n_classes=3).to(self.device)
        self.model.load_state_dict(ck["model_state_dict"])
        self.model.eval()
        self._is_trained = True
        sd = ck.get("scaler")
        if sd and sd.get("mean"):
            self.scaler.mean_ = np.array(sd["mean"])
            self.scaler.scale_ = np.array(sd["scale"])
            self.scaler.n_features_in_ = len(self.scaler.mean_)
            self.scaler_fitted = True
        logger.info(f"📂 已加载: {p}  mode={self.mode}")
        return True

    def load(self, name="transformer_model"):
        if not self.try_load(name):
            raise FileNotFoundError(f"不存在: {name}")
