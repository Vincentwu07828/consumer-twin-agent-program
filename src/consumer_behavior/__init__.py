"""New consumer module, inspired by LifeSim; no upstream code is copied."""

from .behavior import BehaviorModel, ConstantClickModel
from .candidates import Candidate, LocalCandidateProvider
from .feedback import FeedbackRecorder
from .harness import Harness
from .memory import MemoryStore
from .policy import DecisionPolicy, Mode
from .profile import ConsumerProfile, ProfileAttribute
from .state import ConsumerState
from .storage import EventStore
from .restaurants import RestaurantCandidate, RestaurantCandidateProvider, RestaurantConstraintChecker
from .recommendation import RestaurantDecisionModule

__all__ = [
    "BehaviorModel", "ConstantClickModel", "Candidate", "LocalCandidateProvider",
    "FeedbackRecorder", "Harness", "MemoryStore", "DecisionPolicy", "Mode",
    "ConsumerProfile", "ProfileAttribute", "ConsumerState", "EventStore",
    "RestaurantCandidate", "RestaurantCandidateProvider", "RestaurantConstraintChecker", "RestaurantDecisionModule",
]
